import socket
from app import app

def get_local_ip():
    """Dynamically fetches your machine's local network IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return socket.gethostbyname(socket.gethostname())

if __name__ == "__main__":
    host_ip = get_local_ip()
    port = 5000
    
    print("=" * 50)
    print(f" * Hosting Mock LMS via SQLite")
    print(f" * Local Network URL: http://{host_ip}:{port}")
    print(f" * Localhost URL:     http://127.0.0.1:{port}")
    print("=" * 50)
    
    app.run(host=host_ip, port=port, debug=True)