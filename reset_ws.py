"""Force-reset the child's live Showdown websocket TCP connection via the Windows
iphlpapi.SetTcpEntry(MIB_TCP_STATE_DELETE_TCB) API. This sends an RST so the bot's
PSWebsocketClient sees ConnectionClosed and its _auto_reconnect rejoins the battle
rooms -- an in-process disconnect survival, with no process restart. Requires admin.
"""
import ctypes
import socket
import struct
import sys
from ctypes import wintypes

import psutil

MIB_TCP_STATE_DELETE_TCB = 12


class MIB_TCPROW(ctypes.Structure):
    _fields_ = [
        ("dwState", wintypes.DWORD),
        ("dwLocalAddr", wintypes.DWORD),
        ("dwLocalPort", wintypes.DWORD),
        ("dwRemoteAddr", wintypes.DWORD),
        ("dwRemotePort", wintypes.DWORD),
    ]


def addr_dword(ip: str) -> int:
    return struct.unpack("<I", socket.inet_aton(ip))[0]


def port_dword(port: int) -> int:
    return socket.htons(port) & 0xFFFF


def main() -> int:
    # child pids passed as args (the ladder_run.py python procs)
    target_pids = {int(a) for a in sys.argv[1:]} if len(sys.argv) > 1 else None
    found = None
    for c in psutil.net_connections(kind="tcp4"):
        if not c.raddr or c.status != psutil.CONN_ESTABLISHED:
            continue
        if c.raddr.port != 443:
            continue
        if target_pids and c.pid not in target_pids:
            continue
        found = c
        break
    if not found:
        print("NO_MATCHING_CONNECTION")
        return 2
    laddr, lport = found.laddr.ip, found.laddr.port
    raddr, rport = found.raddr.ip, found.raddr.port
    print(f"resetting {laddr}:{lport} -> {raddr}:{rport} (pid {found.pid})", flush=True)

    row = MIB_TCPROW()
    row.dwState = MIB_TCP_STATE_DELETE_TCB
    row.dwLocalAddr = addr_dword(laddr)
    row.dwLocalPort = port_dword(lport)
    row.dwRemoteAddr = addr_dword(raddr)
    row.dwRemotePort = port_dword(rport)

    ret = ctypes.windll.iphlpapi.SetTcpEntry(ctypes.byref(row))
    print(f"SetTcpEntry_ret={ret}  ({'OK - connection reset' if ret == 0 else 'FAILED (5=access denied, 317=not found/state)'})")
    return 0 if ret == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
