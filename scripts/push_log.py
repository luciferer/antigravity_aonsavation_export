import sys
import urllib.request
import json

def push():
    raw_data = sys.stdin.read()
    if not raw_data.strip():
        return
    try:
        # Load from stdin to verify JSON structure
        payload = json.loads(raw_data)
        
        url = "http://localhost:18888/log"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req)
        print("Pushed successfully!")
    except Exception as e:
        print(f"Push failed: {e}")

if __name__ == "__main__":
    push()
