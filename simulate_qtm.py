import argparse
import socket
import time


START = '''<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<CaptureStart><Name VALUE="{name}"/><DatabasePath VALUE=""/><Delay VALUE="0"/>
<PacketID VALUE="{packet_id}"/><HostName VALUE="SIMULATOR"/><ProcessID VALUE="99999"/>
<Notes VALUE=""/><Description VALUE=""/><Timecode VALUE=""/></CaptureStart>'''

STOP = '''<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<CaptureStop RESULT="SUCCESS"><Name VALUE="{name}"/><DatabasePath VALUE=""/><Delay VALUE="0"/>
<PacketID VALUE="{packet_id}"/><HostName VALUE="SIMULATOR"/><ProcessID VALUE="99999"/></CaptureStop>'''


def main():
    parser = argparse.ArgumentParser(description="发送测试用 QTM UDP 开始/停止消息")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8989)
    parser.add_argument("--name", default="SYNC_TEST_001")
    parser.add_argument("--duration", type=float, default=2.0)
    args = parser.parse_args()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(START.format(name=args.name, packet_id="1").encode(), (args.host, args.port))
    print("已发送 CaptureStart")
    time.sleep(args.duration)
    sock.sendto(STOP.format(name=args.name, packet_id="2").encode(), (args.host, args.port))
    print("已发送 CaptureStop")


if __name__ == "__main__":
    main()

