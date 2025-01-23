#!/bin/sh

while true; do
    echo "Building CLI binaries..."
    
    # Build Windows binary
    cargo build --release --target x86_64-pc-windows-gnu
    cp target/x86_64-pc-windows-gnu/release/lic-cli.exe /app/static/cli/lic-cli-windows-x64.exe
    
    # Build Linux binary
    cargo build --release --target x86_64-unknown-linux-gnu
    cp target/x86_64-unknown-linux-gnu/release/lic-cli /app/static/cli/lic-cli-linux-x64
    
    echo "Build complete, sleeping for 1 hour..."
    sleep 3600
done
