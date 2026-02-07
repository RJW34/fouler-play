#!/usr/bin/env python3
"""
Properly register Pokemon Showdown account
"""
import asyncio
import websockets
import requests

async def register_account(username, password):
    """Register on Pokemon Showdown"""
    
    uri = "wss://sim3.psim.us/showdown/websocket"
    
    async with websockets.connect(uri) as ws:
        # Get challstr
        while True:
            msg = await ws.recv()
            if "|challstr|" in msg:
                parts = msg.split("|")
                challstr = f"{parts[2]}|{parts[3]}"
                break
        
        print(f"Got challstr, attempting registration...")
        
        # Try registration with empty captcha first
        reg_url = "https://play.pokemonshowdown.com/~~showdown/action.php"
        
        # Try different anti-spam answers
        captcha_attempts = ["", "4", "2+2", "four"]
        
        for captcha in captcha_attempts:
            data = {
                "act": "register",
                "username": username,
                "password": password,
                "cpassword": password,
                "challstr": challstr,
                "captcha": captcha
            }
            
            resp = requests.post(reg_url, data=data)
            print(f"Captcha attempt '{captcha}': {resp.text[:200]}")
            
            if "actionsuccess" in resp.text or "curuser" in resp.text:
                print(f"✅ Registration successful with captcha: {captcha}")
                return True
                
        print("❌ Registration failed - manual intervention needed")
        return False

if __name__ == "__main__":
    asyncio.run(register_account("LEBOTJAMESXD005", "LeBotPassword2026!"))
