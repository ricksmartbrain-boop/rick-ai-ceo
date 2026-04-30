#!/usr/bin/env python3
"""
moltbook-post.py — Post to Moltbook with automatic verification solving.
Usage: python3 moltbook-post.py --submolt agents --title "Title" --content "Content"
"""
import json, os, sys, re, time, urllib.request, argparse

def load_env():
    for f in [os.path.expanduser("~/.openclaw/workspace/config/rick.env")]:
        if os.path.exists(f):
            for line in open(f):
                line = line.strip()
                if line.startswith("export "): line = line[7:]
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

def api_call(method, path, data=None):
    api_key = os.environ.get("MOLTBOOK_API_KEY", "")
    url = f"https://www.moltbook.com/api/v1{path}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return json.loads(body)
        except:
            return {"error": body, "statusCode": e.code}

def solve_challenge(challenge_text):
    """Parse the obfuscated Moltbook math challenge and return the answer."""
    # Strip ALL non-alpha, non-digit, non-space chars, then lowercase
    # Handles obfuscation like: ]sWiMmS^ -> swims, tHiRrTy -> thirty
    clean = re.sub(r'[^a-zA-Z0-9\s]', ' ', challenge_text).lower()
    # Collapse multiple spaces
    clean = re.sub(r'\s+', ' ', clean).strip()
    # Moltbook obfuscates letters as case-alternation + duplication like 'fIiVvEe' -> 'five',
    # 'tHiRrTy' -> 'thirty'. After lowercasing we get 'fiivvee' / 'thirrty'. Collapse any
    # run of the same letter (>=2) down to a single letter and reparse for number words.
    dedup = re.sub(r'([a-z])\1+', r'\1', clean)
    # Keep both the original and the deduped version so digit extraction still works.
    clean_parsed = dedup
    
    # Extract all numbers (written as words or digits)
    word_to_num = {
        'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4,
        'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9,
        'ten': 10, 'eleven': 11, 'twelve': 12, 'thirteen': 13,
        'fourteen': 14, 'fifteen': 15, 'sixteen': 16, 'seventeen': 17,
        'eighteen': 18, 'nineteen': 19, 'twenty': 20, 'thirty': 30,
        'forty': 40, 'fifty': 50, 'sixty': 60, 'seventy': 70,
        'eighty': 80, 'ninety': 90, 'hundred': 100
    }
    
    # Find digit numbers (use original clean — digits are not deduped)
    digit_nums = [int(x) for x in re.findall(r'\b\d+\b', clean)]
    
    # Find word numbers - handle compounds like "twenty three"
    # Moltbook obfuscation inserts duplicate letters (e.g. 'tHrEe' -> lower 'three',
    # 'fIiVvEe' -> lower 'fiivvee', 'sEcOnDd' -> 'secondd'). Rather than collapse all
    # duplicates (which eats real double letters in 'three' / 'see'), we build a
    # collapse-once dict: any substring where each letter is collapsed to a single
    # occurrence maps to the canonical number word.
    def collapse(s):
        return re.sub(r'([a-z])\1+', r'\1', s)
    collapsed_to_word = {collapse(w): w for w in word_to_num}

    def lookup_num_word(token):
        if token in word_to_num:
            return token
        c = collapse(token)
        if c in collapsed_to_word:
            return collapsed_to_word[c]
        return None

    # Words on the original clean text (keeps real double letters intact)
    words = re.findall(r'[a-z]+', clean)

    # Step 1: merge adjacent short fragments that form a known number word.
    # This handles obfuscation that splits a word across whitespace e.g. 'tw enty' -> 'twenty'.
    def merge_fragments(words):
        merged = []
        i = 0
        while i < len(words):
            found = False
            for j in range(min(i + 5, len(words) + 1), i + 1, -1):
                candidate = ''.join(words[i:j])
                if lookup_num_word(candidate):
                    merged.append(lookup_num_word(candidate))
                    i = j
                    found = True
                    break
            if not found:
                # Normalize single token via collapse-lookup if possible
                merged.append(lookup_num_word(words[i]) or words[i])
                i += 1
        return merged

    words = merge_fragments(words)

    word_nums = []
    i = 0
    while i < len(words):
        w = words[i]
        if w in word_to_num:
            val = word_to_num[w]
            # Check for compound: "twenty three" = 23 (possibly fragmented then merged)
            if val >= 20 and val < 100 and i + 1 < len(words) and words[i+1] in word_to_num:
                next_val = word_to_num[words[i+1]]
                if next_val < 10:
                    val += next_val
                    i += 1
            word_nums.append(val)
        i += 1
    
    all_nums = digit_nums + word_nums
    # Remove duplicates while preserving order
    seen = set()
    nums = []
    for n in all_nums:
        if n not in seen:
            seen.add(n)
            nums.append(n)
    
    if len(nums) < 2:
        print(f"Could not find 2+ numbers in: {challenge_text}")
        print(f"Found: {nums}")
        return None
    
    a, b = nums[0], nums[1]
    
    # Determine operation from context
    # Use deduped text for operation keyword detection too
    clean_ops = clean_parsed
    if any(w in clean_ops for w in ['multiply', 'product', 'times', 'power like']):
        result = a * b
        op = '*'
    elif any(w in clean_ops for w in ['subtract', 'minus', 'less', 'lose', 'loses', 'slow', 'decelerat']):
        result = a - b
        op = '-'
    elif any(w in clean_ops for w in ['divide', 'split', 'ratio']):
        result = a / b if b != 0 else 0
        op = '/'
    else:
        # Default: addition (total, gains, how many, add, plus, sum)
        # If more than 2 numbers and asking for total, sum ALL of them
        if len(nums) > 2 and any(w in clean_ops for w in ['total', 'sum', 'combined', 'together', 'all', 'overall']):
            result = sum(nums)
            op = '+...'
            print(f"  Numbers (sum all): {nums} = {result:.2f}")
        else:
            result = a + b
            op = '+'
    
    # Moltbook requires 2-decimal format (e.g. "20.00"), even for whole numbers.
    answer = f"{result:.2f}"
    print(f"  Numbers: {a} {op} {b} = {answer}")
    return answer

def post(submolt, title, content):
    load_env()
    
    print(f"Posting to m/{submolt}: {title[:50]}...")
    resp = api_call("POST", "/posts", {
        "title": title,
        "content": content,
        "submolt": submolt,
        "submolt_name": submolt
    })
    
    if resp.get("statusCode") == 429:
        wait = resp.get("retry_after_seconds", 60)
        print(f"Rate limited. Waiting {wait}s...")
        time.sleep(wait + 2)
        resp = api_call("POST", "/posts", {
            "title": title,
            "content": content,
            "submolt": submolt,
            "submolt_name": submolt
        })
    
    if not resp.get("success"):
        print(f"Post failed: {resp}")
        return False
    
    post_data = resp.get("post", {})
    post_id = post_data.get("id", "")
    verification = post_data.get("verification", {})
    code = verification.get("verification_code", "")
    challenge = verification.get("challenge_text", "")
    
    print(f"Post created: {post_id}")
    print(f"Status: {post_data.get('verification_status', '?')}")
    
    if code and challenge:
        print(f"Challenge: {challenge}")
        answer = solve_challenge(challenge)
        if answer:
            verify_resp = api_call("POST", "/verify", {
                "verification_code": code,
                "answer": answer
            })
            if verify_resp.get("success"):
                print(f"Verified! Post is live.")
                return True
            else:
                print(f"Verification failed: {verify_resp}")
                return False
        else:
            print("Could not solve challenge")
            return False
    elif post_data.get("verification_status") == "verified":
        print("Auto-verified!")
        return True
    elif post_data.get("verification_status") == "failed":
        # Challenge was never issued (no code/text). This can happen when the API
        # pre-fails verification without giving us a solvable challenge.
        # Confirm whether the post actually exists and is live before failing.
        if post_id:
            time.sleep(3)
            check = api_call("GET", f"/posts/{post_id}")
            p = check.get("post", {})
            if check.get("success") and not p.get("is_deleted") and not p.get("is_spam"):
                print(f"Post submitted (verification_status=failed but post is live). ID: {post_id}")
                return True
        print(f"Post verification failed (status=failed, no code returned). Post ID: {post_id}")
        return False
    else:
        print("No verification needed or already verified")
        return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--submolt", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--content", required=True)
    args = parser.parse_args()
    
    success = post(args.submolt, args.title, args.content)
    sys.exit(0 if success else 1)
