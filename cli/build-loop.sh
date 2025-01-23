#!/bin/sh

check_binary() {
    local file="$1"
    if [ ! -f "$file" ] || [ ! -s "$file" ]; then
        echo "Warning: Build failed or empty binary: $file"
        return 1
    fi
    echo "Successfully built: $file"
    return 0
}

while true; do
    echo "Building CLI binaries..."
    
    # Build Windows binary
    cargo build --release --target x86_64-pc-windows-gnu
    cp -f target/x86_64-pc-windows-gnu/release/lic-cli.exe /app/app/static/cli/lic-cli-windows-x64.exe
    check_binary /app/app/static/cli/lic-cli-windows-x64.exe
    
    # Build Linux binary
    cargo build --release --target x86_64-unknown-linux-gnu
    cp -f target/x86_64-unknown-linux-gnu/release/lic-cli /app/app/static/cli/lic-cli-linux-x64
    check_binary /app/app/static/cli/lic-cli-linux-x64
    
    # Build macOS Intel binary
    cargo build --release --target x86_64-apple-darwin
    cp -f target/x86_64-apple-darwin/release/lic-cli /app/app/static/cli/lic-cli-macos-x64
    check_binary /app/app/static/cli/lic-cli-macos-x64
    
    # Build macOS ARM binary (Apple Silicon)
    cargo build --release --target aarch64-apple-darwin
    cp -f target/aarch64-apple-darwin/release/lic-cli /app/app/static/cli/lic-cli-macos-arm64
    check_binary /app/app/static/cli/lic-cli-macos-arm64
    
    echo "Build complete, sleeping for 1 hour..."
    sleep 3600
done
