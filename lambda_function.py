import requests
from bs4 import BeautifulSoup
import time
import json
import os
import traceback
from dotenv import load_dotenv
import boto3

# --- Configuration ---
load_dotenv() # Load variables from .env file into environment
DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL')
S3_BUCKET_NAME = os.getenv('S3_BUCKET_NAME')
STATE_FILE_KEY = os.getenv('STATE_FILE_KEY', 'course_state.json')
DISCORD_USER_ID_TO_PING = os.getenv('DISCORD_USER_ID_TO_PING')

COURSE_PREFIXES_TO_FETCH = ["cmsc3", "cmsc4"]
SPECIFIC_3XX_COURSES = ["CMSC320", "CMSC335"]
TERM_ID = "202601"
STARRED_COURSES = {
    "CMSC320", "CMSC335", "CMSC414", "CMSC417", "CMSC421",
    "CMSC424", "CMSC430", "CMSC433", "CMSC434", "CMSC435", "CMSC436"
}
COURSES_TO_EXCLUDE = ["CMSC498A", "CMSC499A"]

SOC_SEARCH_URL_TEMPLATE = "https://app.testudo.umd.edu/soc/search?courseId={prefix}&sectionId=&termId={term_id}&creditCompare=&credits=&courseLevelFilter=ALL&instructor=&_facetoface=on&_blended=on&_online=on&courseStartCompare=&courseStartHour=&courseStartMin=&courseStartAM=&courseEndHour=&courseEndMin=&courseEndAM=&teachingCenter=ALL&_classDay1=on&_classDay2=on&_classDay3=on&_classDay4=on&_classDay5=on"
SOC_SECTION_URL_TEMPLATE = "https://app.testudo.umd.edu/soc/{term_id}/sections?courseIds={course_id}"

SEND_DISCORD_NOTIFICATION = True
SEND_NO_UPDATES_MESSAGE = True
SECTION_FETCH_DELAY = 0.5
PARSE_ERROR_DEFAULT = -999

s3_client = boto3.client('s3')

# --- Helper Functions (fetch_initial_page, fetch_section_details, parse_int_safe, process_course_prefixes) ---
# (These functions remain identical to the previous version)
def fetch_initial_page(url):
    """Fetches a search results page HTML using requests (sync)."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        print(f"Fetching course list page: {url}")
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        return soup
    except requests.exceptions.RequestException as e:
        print(f"Error fetching URL {url}: {e}")
        return None
    except Exception as parse_e:
        print(f"Error parsing initial page: {parse_e}")
        return None

def fetch_section_details(course_id, term_id, search_url_base):
    """Fetches section details HTML snippet using requests (sync)."""
    section_url = SOC_SECTION_URL_TEMPLATE.format(term_id=term_id, course_id=course_id)
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Referer': search_url_base,
        'X-Requested-With': 'XMLHttpRequest'
    }
    try:
        response = requests.get(section_url, headers=headers, timeout=25)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        return soup
    except requests.exceptions.RequestException as e:
        print(f"Error fetching sections for {course_id}: {e}")
        return None

def parse_int_safe(text, default=PARSE_ERROR_DEFAULT):
    """Safely converts text to int."""
    if text is None: return default
    try:
        return int(text.strip().replace(',', ''))
    except (ValueError, TypeError):
        return default

def process_course_prefixes(prefixes, specific_3xx, excluded, term_id):
    """Fetches initial pages, filters courses, fetches/parses sections SEQUENTIALLY."""
    all_courses_data = {}
    courses_to_process = {}
    print(f"Processing prefixes sequentially: {', '.join(prefixes)}")
    for prefix in prefixes:
        search_url = SOC_SEARCH_URL_TEMPLATE.format(prefix=prefix, term_id=term_id)
        initial_soup = fetch_initial_page(search_url)
        if not initial_soup: continue
        course_divs = initial_soup.find_all('div', class_='course')
        if not course_divs: continue
        print(f"Found {len(course_divs)} course divs for prefix {prefix}.")
        for course_div in course_divs:
            course_id = None; course_title = "Unknown Title"; course_div_id = None
            course_id_input = course_div.find('input', {'name': 'courseId'})
            if course_id_input and course_id_input.get('value'): course_id = course_id_input['value']
            else: course_div_id = course_div.get('id');
            if course_div_id: course_id = course_div_id
            title_span = course_div.find('span', class_='course-title')
            if title_span: course_title = title_span.text.strip()
            if not course_id: continue
            is_relevant = False
            if course_id in excluded: continue
            elif prefix == "cmsc3" and course_id in specific_3xx: is_relevant = True
            elif prefix == "cmsc4": is_relevant = True
            if is_relevant: courses_to_process[course_id] = course_title
    if not courses_to_process: return {}
    num_courses = len(courses_to_process); print(f"\nCollected {num_courses} relevant course IDs: {', '.join(courses_to_process.keys())}")
    count = 0; search_url_base = SOC_SEARCH_URL_TEMPLATE.format(prefix=prefixes[0], term_id=term_id).split('?')[0]
    for course_id, title in courses_to_process.items():
        count += 1; print(f"Processing: {course_id} ({count}/{num_courses})")
        all_courses_data[course_id] = {"title": title, "sections": {}}
        section_soup = fetch_section_details(course_id, term_id, search_url_base)
        if not section_soup: print(f" -> FETCH ERROR for {course_id}. Marking as error."); all_courses_data[course_id] = {"title": title, "fetch_error": True}; time.sleep(SECTION_FETCH_DELAY); continue
        sections_container = section_soup.find('div', class_='sections-container'); section_divs = sections_container.find_all('div', class_='section') if sections_container else section_soup.find_all('div', class_='section')
        if not section_divs: print(f" -> No section divs found in snippet for {course_id}")
        else:
            for section_div in section_divs:
                sec_id_span = section_div.find('span', class_='section-id'); opn_span = section_div.find('span', class_='open-seats-count'); tot_span = section_div.find('span', class_='total-seats-count'); wl_span = section_div.find('span', class_='waitlist-count'); instr_span = section_div.find('span', class_='section-instructor')
                sec_id = sec_id_span.text.strip() if sec_id_span else None; instr = "Instructor: TBA"
                if instr_span: link = instr_span.find('a'); raw = link.text.strip() if link else instr_span.text.strip();
                if raw and "Instructor: TBA" not in raw and raw.strip(): instr = raw
                opn = parse_int_safe(opn_span.text if opn_span else None); tot = parse_int_safe(tot_span.text if tot_span else None); wl = parse_int_safe(wl_span.text if wl_span else None)
                if sec_id: all_courses_data[course_id]["sections"][sec_id] = {"open": opn, "total": tot, "waitlist": wl, "instructor": instr}
                else: print(f"   -> Could not find section_id span for {course_id}")
        time.sleep(SECTION_FETCH_DELAY)
    return all_courses_data

# --- S3 State Management ---
def load_previous_state_s3():
    if not S3_BUCKET_NAME: print("S3_BUCKET_NAME not set."); return {}
    try:
        print(f"Loading state from s3://{S3_BUCKET_NAME}/{STATE_FILE_KEY}"); response = s3_client.get_object(Bucket=S3_BUCKET_NAME, Key=STATE_FILE_KEY)
        state_data = json.loads(response['Body'].read().decode('utf-8')); print("State loaded from S3."); return state_data
    except s3_client.exceptions.NoSuchKey: print(f"State file '{STATE_FILE_KEY}' not found in S3."); return {}
    except Exception as e: print(f"Error loading state from S3: {e}"); traceback.print_exc(); return {}

def save_current_state_s3(data):
    if not S3_BUCKET_NAME: print("S3_BUCKET_NAME not set."); return False
    try:
        print(f"Saving state to s3://{S3_BUCKET_NAME}/{STATE_FILE_KEY}"); s3_client.put_object(Bucket=S3_BUCKET_NAME, Key=STATE_FILE_KEY, Body=json.dumps(data, indent=2), ContentType='application/json')
        print("State saved to S3."); return True
    except Exception as e: print(f"Error saving state to S3: {e}"); traceback.print_exc(); return False

# --- Comparison ---
# (compare_states remains identical, it correctly identifies *what* changed)
def compare_states(old_state, new_state):
    """Compares states, returns list of change dicts for relevant changes."""
    changes = []; default_section = {"open": PARSE_ERROR_DEFAULT, "total": PARSE_ERROR_DEFAULT, "waitlist": PARSE_ERROR_DEFAULT, "instructor": "Unknown"}
    for course_id, new_course_data in new_state.items():
        if new_course_data.get("fetch_error"): continue
        new_title = new_course_data.get("title", "Unknown"); new_sections = new_course_data.get("sections", {})
        if course_id not in old_state:
            change_type = "NEW_CMSC4_COURSE" if course_id.startswith("CMSC4") else "NEW_COURSE_SECTION"
            for section_id, section_data in new_sections.items(): changes.append({"type": change_type,"course": course_id,"title": new_title,"section": section_id,"data": section_data}); continue
        old_course_data = old_state.get(course_id, {}); old_sections = old_course_data.get("sections", {})
        for section_id, new_section_data in new_sections.items():
            if section_id not in old_sections: changes.append({"type": "NEW_SECTION","course": course_id,"title": new_title,"section": section_id,"data": new_section_data})
            else:
                old_section_data = old_sections.get(section_id, default_section)
                old_open = old_section_data.get("open", PARSE_ERROR_DEFAULT); new_open = new_section_data.get("open", PARSE_ERROR_DEFAULT)
                if old_open == 0 and new_open > 0: changes.append({"type": "SEATS_OPENED","course": course_id,"title": new_title,"section": section_id,"data": new_section_data, "old_val": old_open, "new_val": new_open, "field": "open"})
                elif old_open != new_open and new_open != PARSE_ERROR_DEFAULT and old_open != PARSE_ERROR_DEFAULT: changes.append({"type": "OPEN_CHANGE","course": course_id,"title": new_title,"section": section_id,"data": new_section_data, "old_val": old_open, "new_val": new_open, "field": "open"})
                old_total = old_section_data.get("total", PARSE_ERROR_DEFAULT); new_total = new_section_data.get("total", PARSE_ERROR_DEFAULT)
                if old_total != new_total and new_total != PARSE_ERROR_DEFAULT and old_total != PARSE_ERROR_DEFAULT: changes.append({"type": "TOTAL_CHANGE","course": course_id,"title": new_title,"section": section_id,"data": new_section_data, "old_val": old_total, "new_val": new_total, "field": "total"})
                old_wait = old_section_data.get("waitlist", PARSE_ERROR_DEFAULT); new_wait = new_section_data.get("waitlist", PARSE_ERROR_DEFAULT)
                if old_wait != new_wait and new_wait != PARSE_ERROR_DEFAULT and old_wait != PARSE_ERROR_DEFAULT: changes.append({"type": "WAITLIST_CHANGE","course": course_id,"title": new_title,"section": section_id,"data": new_section_data, "old_val": old_wait, "new_val": new_wait, "field": "waitlist"})
                old_instr = old_section_data.get("instructor", "Unknown"); new_instr = new_section_data.get("instructor", "TBA")
                if old_instr != new_instr and old_instr != "Unknown": changes.append({"type": "INSTR_CHANGE","course": course_id,"title": new_title,"section": section_id,"data": new_section_data, "old_val": old_instr, "new_val": new_instr, "field": "instructor"})
    for course_id, old_course_data in old_state.items():
        if course_id in new_state and not new_state[course_id].get("fetch_error"):
            old_sections = old_course_data.get("sections", {}); new_sections = new_state[course_id].get("sections", {})
            for section_id, old_section_data in old_sections.items():
                if section_id not in new_sections: changes.append({"type": "SECTION_REMOVED", "course": course_id, "title": old_course_data.get("title", "Unknown"), "section": section_id, "data": old_section_data})
    return changes

# --- Formatting ---
def get_status_emoji(open_seats, total_seats):
    if open_seats == 0: return "🔴 "
    elif open_seats > 0 and open_seats < total_seats: return "⏳ "
    else: return "" # Fully open, no emoji needed

# --- NEW: Helper function to build a lookup dict for changes ---
def build_change_lookup(changes):
    """Converts the flat list of change dicts into a nested lookup dictionary."""
    lookup = {}
    for change in changes:
        key = (change['course'], change['section'])
        if key not in lookup:
            lookup[key] = []
        lookup[key].append(change)
    return lookup

def format_state_message(state_data, change_lookup={}):
    """
    Formats the entire current state into a Discord message.
    If change_lookup is provided, it annotates the changes.
    """
    if not state_data: return "**State Message**: No courses found/parsed."
    
    # Determine header based on if changes exist
    if change_lookup:
        header = f"**📊 Course Section Update:**"
    else:
        header = f"**📊 Initial State ({len(state_data)} courses monitored):**"
        
    lines = [header]
    
    for course_id, course_data in sorted(state_data.items()):
        star = "⭐ " if course_id in STARRED_COURSES else ""
        title = course_data.get("title", "Unknown Title")
        sections = course_data.get("sections", {})
        title_short = (title[:45-len(course_id)] + "...") if len(title) > (45-len(course_id)) else title
        lines.append(f"\n{star}**`{course_id}`** ({title_short}):")
        
        if course_data.get("fetch_error"):
            lines.append("  • ⚠️ *(Fetch Error: Data may be stale)*")
            continue
            
        if not sections:
            lines.append("  • *(No sections found/parsed.)*")
            continue

        # Format sections
        for section_id, data in sorted(sections.items()):
            # Get changes for this specific section
            section_changes = change_lookup.get((course_id, section_id), [])
            lines.append(format_section_line(section_id, data, section_changes))

    # --- Append removed sections to the message if there are changes ---
    if change_lookup:
        removed_lines = []
        for change in changes: # This was the bug, iterating over the wrong variable
            if change['type'] == "SECTION_REMOVED":
                # Format removed sections using a simplified version of format_change_message
                data = change["data"] # Use the data from the change dict
                opn = data.get("open", "?") if data.get("open", PARSE_ERROR_DEFAULT) != PARSE_ERROR_DEFAULT else "?"
                tot = data.get("total", "?") if data.get("total", PARSE_ERROR_DEFAULT) != PARSE_ERROR_DEFAULT else "?"
                wl = data.get("waitlist", "?") if data.get("waitlist", PARSE_ERROR_DEFAULT) != PARSE_ERROR_DEFAULT else "?"
                instr = data.get("instructor", "TBA")
                star = "⭐ " if change["course"] in STARRED_COURSES else ""
                removed_lines.append(f"{star}❌ REMOVED: `{change['course']}` Sec `{change['section']}` (was Open: {opn}, Total: {tot}, Waitlist: {wl}, Instr: {instr})")

        if removed_lines:
            lines.append("\n**Removed Sections:**")
            lines.append("```") 
            lines.extend(removed_lines)
            lines.append("```")
            
    return "\n".join(lines)


# --- NEW: Helper function to format a single section line (with or without changes) ---
def format_section_line(section_id, data, changes):
    """Formats a single section line, applying annotations if changes exist."""
    opn = data.get("open", PARSE_ERROR_DEFAULT); tot = data.get("total", PARSE_ERROR_DEFAULT)
    wl = data.get("waitlist", PARSE_ERROR_DEFAULT); instr = data.get("instructor", "TBA")
    
    opn_str_val = str(opn) if opn != PARSE_ERROR_DEFAULT else "?"; tot_str_val = str(tot) if tot != PARSE_ERROR_DEFAULT else "?"; wl_str_val = str(wl) if wl != PARSE_ERROR_DEFAULT else "?"
    
    # Base strings
    open_str = f"Open: {opn_str_val}"; total_str = f"Total: {tot_str_val}"
    wait_str = f"Waitlist: {wl_str_val}"; instr_str = f"Instr: {instr}"
    
    change_tags = [] # To store tags like [OPENED], [NEW]
    
    if changes:
        for change in changes:
            field = change.get("field"); old_val = change.get("old_val"); new_val = change.get("new_val")
            diff_str = ""
            if field and old_val is not None and new_val is not None and old_val != PARSE_ERROR_DEFAULT and new_val != PARSE_ERROR_DEFAULT:
                try: diff = int(new_val) - int(old_val); diff_str = f" ({diff:+}d)" if diff != 0 or change["type"] == "SEATS_OPENED" else ""
                except (ValueError, TypeError): diff_str = " (?)"

            # Apply bolding and tags
            if change["type"] == "SEATS_OPENED":
                open_str = f"**Open: {new_val}**{diff_str}"; change_tags.append("🟢 OPENED")
            elif change["type"] == "OPEN_CHANGE":
                open_str = f"**Open: {new_val}**{diff_str}"
            elif change["type"] == "TOTAL_CHANGE":
                total_str = f"**Total: {new_val}**{diff_str}"
            elif change["type"] == "WAITLIST_CHANGE":
                wait_str = f"**Waitlist: {new_val}**{diff_str}"
            elif change["type"] == "INSTR_CHANGE":
                instr_str = f"**Instr: {new_val}** (was {old_val})"
            elif change["type"] == "NEW_SECTION":
                change_tags.append("➕ NEW")
            elif change["type"] == "NEW_COURSE_SECTION":
                change_tags.append("✨ NEW CRS")
            elif change["type"] == "NEW_CMSC4_COURSE":
                change_tags.append("🚨 NEW CMSC4")

    # Determine status emoji
    status_emoji = ""
    if opn != PARSE_ERROR_DEFAULT and tot != PARSE_ERROR_DEFAULT: status_emoji = get_status_emoji(opn, tot)
    elif opn == 0: status_emoji = "🔴 "
    
    # Add GREEN emoji if it just opened, overriding others
    if "🟢 OPENED" in change_tags:
        status_emoji = "🟢 "
        
    tags_str = f" *({', '.join(change_tags)})*" if change_tags else "" # Format tags like *(OPENED, NEW)*

    # Assemble final line
    return f"  • {status_emoji}`{section_id}`: {open_str}, {total_str}, {wait_str}, {instr_str}{tags_str}"


# --- Discord Notification ---
def send_discord_notification(message_content, has_changes=False, is_initial_state=False, is_error_message=False, is_no_updates=False):
    """Sends a pre-formatted message to Discord, handling pings and splitting."""
    if not DISCORD_WEBHOOK_URL: print("Discord Webhook URL not found. Skipping."); return

    user_ping = ""
    # Add ping only if it's an update message (has_changes) and ID is set
    if has_changes and DISCORD_USER_ID_TO_PING:
        user_ping = f"<@{DISCORD_USER_ID_TO_PING}> "

    # --- Split and Send logic ---
    max_len = 1950; messages_to_send = []
    # Prepend ping to the *first line* of the message content if applicable
    lines = message_content.splitlines()
    if not lines: return
    lines[0] = user_ping + lines[0] # Add ping to the first line
    
    current_message_part = ""

    for i, line in enumerate(lines):
        # Check if adding the line exceeds max length
        if len(current_message_part) + len(line) + 1 > max_len:
            # Send the current part if it's not empty
            if current_message_part: messages_to_send.append(current_message_part)
            # Start new part
            current_message_part = line + "\n"
        else:
            # Add line to current part
            current_message_part += line + "\n"

    # Add the last part
    if current_message_part: messages_to_send.append(current_message_part)

    # Send the assembled message parts
    success = True
    for part in messages_to_send:
        if not part.strip(): continue
        payload = {"content": part}
        if user_ping: payload["allowed_mentions"] = {"users": [DISCORD_USER_ID_TO_PING]}
        try:
            response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=15); response.raise_for_status()
            print(f"Discord part sent! (Length: {len(part)})"); time.sleep(1.2)
        except requests.exceptions.RequestException as e:
            print(f"Error sending Discord part: {e}")
            if hasattr(e, 'response') and e.response is not None: print(f"Response: {e.response.status_code} - {e.response.text}")
            success = False; break
    if success and messages_to_send: print("All Discord parts sent.")

# --- Lambda Handler ---
def lambda_handler(event, context):
    """AWS Lambda entry point."""
    start_time = time.time()
    print(f"Lambda function started at {time.ctime()}...")
    if not S3_BUCKET_NAME: print("🛑 ERROR: S3_BUCKET_NAME env var not set.");
    if SEND_DISCORD_NOTIFICATION and not DISCORD_WEBHOOK_URL: print("🛑 ERROR: DISCORD_WEBHOOK_URL env var not set.");
    if SEND_DISCORD_NOTIFICATION and not DISCORD_USER_ID_TO_PING: print("⚠️ WARNING: DISCORD_USER_ID_TO_PING not set. Update notifications will not ping.")

    old_state = load_previous_state_s3()
    fetched_state = process_course_prefixes(COURSE_PREFIXES_TO_FETCH, SPECIFIC_3XX_COURSES, COURSES_TO_EXCLUDE, TERM_ID)

    new_state = {}; fetch_errors = []; processed_courses_ids = set(fetched_state.keys())
    for course_id, data in fetched_state.items():
        if data.get("fetch_error"):
            fetch_errors.append(course_id)
            if course_id in old_state:
                print(f" -> Reusing old state data for {course_id} due to fetch error.")
                new_state[course_id] = old_state[course_id] # Reuse old data
            else:
                 print(f" -> Fetch error for new course {course_id}. No old data available.")
                 new_state[course_id] = data # Keep error marker but no sections
        else:
            new_state[course_id] = data # Use fresh data
    # Retain old courses that weren't in the fetched list at all
    for course_id, old_data in old_state.items():
        if course_id not in fetched_state: # Use fetched_state keys here
             print(f" -> Course {course_id} missing from current fetch results; retaining old data.")
             new_state[course_id] = old_data # Retain old data entirely
             if course_id not in fetch_errors: fetch_errors.append(f"{course_id} (missing from fetch)")

    print("\n--- Final State Summary ---"); num_courses = len(new_state); num_sections = sum(len(d.get("sections", {})) for d in new_state.values())
    print(f"Processed {num_courses} courses, {num_sections} sections.");
    if fetch_errors: print(f"Note: Data for {len(fetch_errors)} course(s) may be stale: {', '.join(fetch_errors)}")
    print("---------------------------\n")

    parsing_successful = any(d.get("sections") for d in new_state.values()) or not processed_courses_ids
    current_status_code = 200

    if not parsing_successful and processed_courses_ids:
        if old_state:
            print("WARN: Parsing failed (no sections found). Skipping update.")
            if SEND_DISCORD_NOTIFICATION: send_discord_notification("Error Alert ⚠️: Failed parsing sections. Check logs.", is_error_message=True)
            return {'statusCode': 200, 'body': json.dumps('Parsing failed, skipped update.')}
        else:
            print("ERROR: Failed parsing sections on first run.")
            if SEND_DISCORD_NOTIFICATION: send_discord_notification("Error Alert ⚠️: Failed parsing sections on initial run.", is_error_message=True)
            return {'statusCode': 500, 'body': json.dumps('Failed parsing on initial run.')}

    if not old_state:
        print("First run successful. Initializing state in S3.")
        initial_summary = format_state_message(new_state, change_lookup={}) # Pass empty lookup
        if SEND_DISCORD_NOTIFICATION:
             send_discord_notification(initial_summary, is_initial_state=True)
             if fetch_errors: send_discord_notification(f"⚠️ Initial fetch failed/stale for: {', '.join(fetch_errors)}.", is_error_message=True)
        save_success = save_current_state_s3(new_state); print("Initial state " + ("saved." if save_success else "FAILED to save."))
        if not save_success: current_status_code = 500
    else:
        changes = compare_states(old_state, new_state)
        if changes:
            print("\n--- CHANGES DETECTED ---"); [print(format_section_line(c['section'], c['data'], [c])) for c in changes if c.get('section')]; print("------------------------\n") # Simplified console log
            if SEND_DISCORD_NOTIFICATION:
                change_lookup = build_change_lookup(changes)
                update_message = format_state_message(new_state, change_lookup) # Format full state with annotations
                send_discord_notification(update_message, has_changes=True) # Send the full message, flagging it as an update
                if fetch_errors: send_discord_notification(f"⚠️ Some course data may be stale due to fetch errors: {', '.join(fetch_errors)}.", is_error_message=True)
            save_success = save_current_state_s3(new_state); print("Changes detected. State " + ("saved." if save_success else "FAILED to save."))
            if not save_success: current_status_code = 500
        else:
            print("No significant changes detected.")
            if SEND_DISCORD_NOTIFICATION:
                if SEND_NO_UPDATES_MESSAGE: send_discord_notification(f"✅ No course section updates found at {time.strftime('%H:%M:%S UTC')}.", is_no_updates=True)
                if fetch_errors: send_discord_notification(f"⚠️ No changes, but fetch failed/stale for: {', '.join(fetch_errors)}.", is_error_message=True)
            if fetch_errors:
                 save_success = save_current_state_s3(new_state); print("Saving merged state " + ("succeeded." if save_success else "FAILED."))
                 if not save_success: current_status_code = 500
            else: print("No state save needed.")

    end_time = time.time(); duration = end_time - start_time
    print(f"Lambda function finished. Duration: {duration:.2f} seconds.")
    return {'statusCode': current_status_code, 'body': json.dumps(f'Scraper run complete. Duration: {duration:.2f}s')}

# --- Local execution block ---
if __name__ == "__main__":
    print("Running script locally...")
    if not DISCORD_WEBHOOK_URL: print("🛑 WARNING: DISCORD_WEBHOOK_URL not found.")
    if SEND_DISCORD_NOTIFICATION and not DISCORD_USER_ID_TO_PING: print("⚠️ WARNING: DISCORD_USER_ID_TO_PING not set in .env file. Update notifications will not ping.")
    if not S3_BUCKET_NAME:
         print("🛑 WARNING: S3_BUCKET_NAME not found. Using local file 'course_state_local.json'.")
         STATE_FILE = "course_state_local.json"
         def load_previous_state_local():
             try:
                 with open(STATE_FILE, 'r') as f: return json.load(f)
             except (FileNotFoundError, json.JSONDecodeError): return {}
         def save_current_state_local(data):
             try:
                 with open(STATE_FILE, 'w') as f: json.dump(data, f, indent=2); return True
             except IOError as e: print(f"Error saving local state: {e}"); return False
         load_previous_state_s3 = load_previous_state_local
         save_current_state_s3 = save_current_state_local
    else:
         print(f"Using S3 bucket '{S3_BUCKET_NAME}' for state.")
         load_previous_state_s3 = load_previous_state_s3
         save_current_state_s3 = save_current_state_s3
    lambda_handler({}, {})

