import requests
import json

def test_api():
    try:
        response = requests.get('http://localhost:8006/api/parcels')
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"Total parcels found: {len(data)}")
            if len(data) > 0:
                print("First parcel sample:")
                print(json.dumps(data[0], indent=2))
        else:
            print(f"Response: {response.text}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_api()
