import argparse

from .server import make_server


def main():
    parser = argparse.ArgumentParser(description="Explore authorized saved web artifacts locally. No network retrieval or execution.")
    parser.add_argument("--port", type=int, default=8768)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535")
    try:
        server = make_server(args.port)
    except OSError:
        parser.error("Could not bind the local port; select another port")
    print(f"Surface Map: http://127.0.0.1:{args.port}")
    print("Offline artifact analysis only. Ctrl+C stops the server.")
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
