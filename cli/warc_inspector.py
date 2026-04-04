"""
CLI tool to inspect WARC/WARC.GZ files using warcio.
"""
import argparse
import sys
from collections import Counter
from warcio.archiveiterator import ArchiveIterator


def get_parser():
    parser = argparse.ArgumentParser(
        prog="warc-inspector",
        description="Inspect WARC/WARC.GZ files",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    read_parser = subparsers.add_parser("read", help="Read and display WARC records")
    read_parser.add_argument("file", help="Path to WARC or WARC.GZ file")
    read_parser.add_argument("--offset", type=int, default=0, help="Skip first N records (default: 0)")
    read_parser.add_argument("--limit", type=int, default=10, help="Number of records to display (default: 10)")
    read_parser.add_argument("--url", type=str, default=None, help="Filter records by WARC-Target-URI (exact match)")
    read_parser.add_argument("--show-content", action="store_true", help="Print record payload content")
    read_parser.add_argument("--content-length", type=int, default=500, help="Max bytes of content to show (default: 500)")

    stats_parser = subparsers.add_parser("stats", help="Show statistics about WARC records")
    stats_parser.add_argument("file", help="Path to WARC or WARC.GZ file")
    stats_parser.add_argument("--top", type=int, default=20, help="Top N URLs to display (default: 20)")

    return parser


def _iter_records(path):
    with open(path, "rb") as f:
        yield from ArchiveIterator(f)


def cmd_read(args):
    matched = 0
    end = args.offset + args.limit
    for i, record in enumerate(_iter_records(args.file)):
        uri = record.rec_headers.get_header("WARC-Target-URI") or "-"

        if args.url and uri != args.url:
            continue

        if matched < args.offset:
            matched += 1
            continue
        if matched >= end:
            break

        date = record.rec_headers.get_header("WARC-Date") or "-"
        content_type = record.http_headers.get_header("Content-Type") if record.http_headers else "-"

        print(f"[{i}] type={record.rec_type}  uri={uri}")
        print(f"     date={date}  content-type={content_type}  length={record.length}")

        if args.show_content and record.rec_type in ("response", "resource", "conversion"):
            try:
                payload = record.content_stream().read(args.content_length)
                print(f"     --- content (first {args.content_length} bytes) ---")
                print(payload.decode("utf-8", errors="replace"))
            except Exception as e:
                print(f"     [could not read content: {e}]")

        print()
        matched += 1

    return 0


def cmd_stats(args):
    total = 0
    type_counts = Counter()
    url_counts = Counter()
    status_counts = Counter()

    for record in _iter_records(args.file):
        total += 1
        type_counts[record.rec_type] += 1

        uri = record.rec_headers.get_header("WARC-Target-URI")
        if uri:
            url_counts[uri] += 1

        if record.http_headers:
            status = record.http_headers.get_statuscode()
            if status:
                status_counts[status] += 1

    print(f"Total records : {total}")
    print(f"Unique URLs   : {len(url_counts)}")
    print()

    print("Record types:")
    for rec_type, count in type_counts.most_common():
        print(f"  {rec_type:<16} {count}")
    print()

    if status_counts:
        print("HTTP status codes:")
        for status, count in sorted(status_counts.items()):
            print(f"  {status}  {count}")
        print()

    if url_counts:
        print(f"Top {args.top} URLs:")
        for url, count in url_counts.most_common(args.top):
            print(f"  {count:>5}  {url}")

    return 0


def main(args=None):
    parser = get_parser()
    parsed = parser.parse_args(args)

    if parsed.command is None:
        parser.print_help()
        return 1

    if parsed.command == "read":
        return cmd_read(parsed)
    elif parsed.command == "stats":
        return cmd_stats(parsed)

    return 0  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
