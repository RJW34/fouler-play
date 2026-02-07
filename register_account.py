#!/usr/bin/env python3
"""
Register a new Pokemon Showdown account
"""
import asyncio
import websockets
import json
import requests
import hashlib

async def register_showdown_account(username, password):
    """Register a new account on Pokemon Showdown"""
    
    uri = "wss://sim3.psim.us/showdown/websocket"
    
    async with websockets.connect(uri) as websocket:
        # Wait for initial messages
        while True:
            message = await websocket.recv()
            print(f"Received: {message[:100]}...")
            
            # Look for challstr
            if message.startswith("|challstr|"):
                parts = message.split("|")
                challstr = parts[2] + "|" + parts[3]
                print(f"Got challstr: {challstr[:50]}...")
                
                # Register account using action.php
                # First, try to login to see if account exists
                print(f"\nAttempting to register account: {username}")
                
                # Pokemon Showdown registration endpoint
                register_url = "https://play.pokemonshowdown.com/action.php"
                
                register_data = {
                    "act": "register",
                    "username": username,
                    "password": password,
                    "cpassword": password,
                    "challstr": challstr,
                    "captcha": ""  # May need captcha for new registrations
                }
                
                response = requests.post(register_url, data=register_data)
                print(f"\nRegistration response: {response.text}")
                
                # Also try to login to confirm
                login_data = {
                    "act": "login",
                    "name": username,
                    "pass": password,
                    "challstr": challstr
                }
                
                login_response = requests.post(register_url, data=login_data)
                print(f"\nLogin verification: {login_response.text}")
                
                break

if __name__ == "__main__":
    username = "LEBOTJAMESXD005"
    password = "LeBotPassword2026!"
    
    print(f"Registering Pokemon Showdown account: {username}")
    asyncio.run(register_showdown_account(username, password))
