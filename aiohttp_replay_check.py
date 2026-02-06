import asyncio
import aiohttp
import json

async def main():
    url = 'https://replay.pokemonshowdown.com/gen9ou-2532208660.json'
    timeout = aiohttp.ClientTimeout(total=5)
    headers = {'User-Agent': 'FoulerPlayOBS/1.0'}
    out = {}
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                out['status'] = resp.status
                out['text_prefix'] = (await resp.text())[:80]
    except Exception as e:
        out['error'] = repr(e)
    with open('aiohttp_replay_check.json', 'w') as f:
        f.write(json.dumps(out, indent=2))

asyncio.run(main())
