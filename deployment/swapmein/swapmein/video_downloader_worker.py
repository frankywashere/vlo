"""Isolated yt-dlp entrypoint. Deny connections to private/metadata networks."""
from pathlib import Path
import ipaddress
import socket
import sys


def public_connection(event, args):
    if event != "socket.connect":
        return
    address = args[1]
    if not isinstance(address, tuple):
        raise PermissionError("Only public network connections are allowed")
    host = address[0]
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        addresses = [ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host, address[1])]
    if not addresses or any(not address.is_global for address in addresses):
        raise PermissionError("Private network destinations are not allowed")


def main():
    sys.addaudithook(public_connection)
    from yt_dlp import main as run
    url, directory, size = sys.argv[1:4]
    cookie_args = []
    if len(sys.argv) > 4:
        cookies = Path(sys.argv[4])
        if cookies.is_symlink() or cookies.resolve() != (Path(directory) / ".youtube-session.txt").resolve():
            raise ValueError("Invalid session file")
        cookie_args = ["--cookies", str(cookies)]
    size = int(size)
    if not 1 <= size <= 750:
        raise ValueError("Invalid download size limit")
    run(["--ignore-config", "--no-playlist", "--playlist-items", "1", "--no-cache-dir",
         "--no-progress", "--socket-timeout", "20", "--retries", "1", "--fragment-retries", "1",
         "--max-filesize", f"{size}M", "--match-filter", "!is_live & duration <=? 1800",
         "--downloader", "native", "--hls-prefer-native", "--use-extractors", "default,-generic",
         "-S", "vcodec:h264,res,acodec:m4a",
         "-f", "bestvideo[vcodec^=avc1]+bestaudio[ext~='^(m4a|mp4)$']/best[vcodec^=avc1]/best[ext=mp4]/bestvideo+bestaudio/best",
         "--merge-output-format", "mp4/mkv",
         "-o", directory + "/video.%(ext)s", *cookie_args, "--", url])


if __name__ == "__main__":
    main()
