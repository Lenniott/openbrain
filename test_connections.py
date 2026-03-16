# save as test_connections.py and run: python test_connections.py
import socket

targets = [
    ("192.168.0.47", 5432,  "postgres"),
    ("192.168.0.47", 6333,  "qdrant"),
    ("192.168.0.47", 9002,  "minio"),
    ("192.168.0.47", 11434, "ollama"),
    ("192.168.0.47", 7799,  "faster-whisper"),
]

for host, port, name in targets:
    try:
        with socket.create_connection((host, port), timeout=3):
            print(f"OK   {name:14} {host}:{port}")
    except Exception as e:
        print(f"FAIL {name:14} {host}:{port} -> {e}")