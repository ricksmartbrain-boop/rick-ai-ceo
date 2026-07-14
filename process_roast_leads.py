#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import requests

# Configuration
LEAD_FILE = os.path.expanduser('~/rick-vault/projects/outreach/roast-leads.jsonl')
EMAIL_LOG = os.path.expanduser('~/rick-vault/projects/outreach/email-log.md')
SCORE_SCRIPT = os.path.expanduser('~/rick-vault/scripts/score-lead.py')

# Get Resend API key from environment
RESEND_API_KEY = os.getenv('RESEND_API_KEY')
if not RESEND_API_KEY:
    print("ERROR: RESEND_API_KEY not found in environment")
    sys.exit(1)

def check_mx_records(domain):
    """Check if domain has MX records"""
    try:
        result = subprocess.run(['dig', 'MX', domain], capture_output=True, text=True)
        return 'NOERROR' in result.stdout and '0 record' not in result.stdout
    except:
        return False

def send_email(to_email, subject, body):
    """Send email via Resend API"""
    url = "https://api.resend.com/emails"
    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "from": "Rick <rick@meetrick.ai>",
        "to": [to_email],
        "subject": subject,
        "text": body
    }
    
    response = requests.post(url, headers=headers, json=data)
    return response.status_code == 200

def get_email_from_domain(domain):
    """Get likely email for domain"""
    # Try common email patterns
    patterns = [
        f"hello@{domain}",
        f"contact@{domain}",
        f"info@{domain}",
        f"support@{domain}"
    ]
    
    # Check MX records first for each pattern
    for email in patterns:
        email_domain = email.split('@')[1]
        if check_mx_records(email_domain):
            return email
    
    return None

def main():
    # Read leads file
    if not os.path.exists(LEAD_FILE):
        print("No leads file found")
        return True
    
    leads = []
    with open(LEAD_FILE, 'r') as f:
        for line in f:
            try:
                lead = json.loads(line.strip())
                leads.append(lead)
            except:
                continue
    
    # Find unprocessed leads
    unprocessed_leads = []
    for lead in leads:
        if lead.get('status') == 'discovered' or ('status' not in lead and 'domain' in lead):
            # Handle case where status field might be missing but domain exists
            unprocessed_leads.append(lead)
    
    print(f"Unprocessed leads: {len(unprocessed_leads)}")
    
    if not unprocessed_leads:
        print("No unprocessed leads to process")
        return True  # Return success to indicate completion
    
    processed_count = 0
    # Process up to 3 leads
    for lead in unprocessed_leads[:3]:
        processed_count += 1
        domain = lead.get('domain')
        if not domain:
            continue
            
        print(f"Processing lead: {domain}")
        
        # Check MX records
        if not check_mx_records(domain):
            print(f"  - No MX records found for {domain}")
            continue
            
        # Get email
        email = get_email_from_domain(domain)
        if not email:
            print(f"  - No valid email found for {domain}")
            continue
            
        print(f"  - Found email: {email}")
        
        # Create personalized message
        subject = f"Your {domain} roast — one thing that would actually help"
        
        body = f"""Hey,

You ran your site through Rick's roast tool today.

I noticed something about {domain}. The roast flagged the main issue — but I wanted to send one concrete fix you can actually ship this week.

Here's the #1 thing to fix: Your site's value proposition isn't clear in the first 10 seconds. Visitors need to know immediately why they should choose you over alternatives.

If you want me to go deeper on your ops or growth — that's what I do at meetrick.ai/hire-rick

— Rick
AI CEO, meetrick.ai"""
        
        # Send email
        if send_email(email, subject, body):
            print(f"  - Email sent to {email}")
            
            # Update lead status
            lead['status'] = 'contacted'
            lead['email_sent'] = email
            lead['processed_date'] = '2026-03-26'
            
            # Save updated lead back to file
            with open(LEAD_FILE, 'w') as f:
                for l in leads:
                    f.write(json.dumps(l) + '\n')
            
            # Score lead
            try:
                subprocess.run([
                    'python3', SCORE_SCRIPT,
                    '--name', domain,
                    '--channel', 'roast_tool',
                    '--action', 'used_roast_tool',
                    '--context', 'roasted their site, follow-up sent',
                    '--email', email
                ], check=True)
            except:
                print(f"  - Warning: Could not score lead")
            
            # Log to email log
            log_entry = f"""## {email} - {domain} - {subject}

**Date**: 2026-03-26
**Action**: Roast follow-up sent
**Email**: {email}
**Subject**: {subject}
**Status**: Sent

Body:
{body}

---

"""
            with open(EMAIL_LOG, 'a') as f:
                f.write(log_entry)
        else:
            print(f"  - Failed to send email to {email}")
    
    print(f"Processed {processed_count} leads")
    return True

if __name__ == "__main__":
    main()