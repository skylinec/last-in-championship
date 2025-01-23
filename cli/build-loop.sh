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
    echo "Building CLI binaries in parallel..."
    
    # Start all builds in parallel
    cargo build --release --target x86_64-pc-windows-gnu &
    windows_pid=$!
    
    cargo build --release --target x86_64-unknown-linux-gnu &
    linux_pid=$!
    
    cargo build --release --target x86_64-apple-darwin &
    mac_x64_pid=$!
    
    cargo build --release --target aarch64-apple-darwin &
    mac_arm_pid=$!
    
    # Wait for all builds to complete
    wait $windows_pid $linux_pid $mac_x64_pid $mac_arm_pid
    
    # Copy binaries after all builds complete
    cp -f target/x86_64-pc-windows-gnu/release/lic-cli.exe /app/app/static/cli/lic-cli-windows-x64.exe
    check_binary /app/app/static/cli/lic-cli-windows-x64.exe
    
    cp -f target/x86_64-unknown-linux-gnu/release/lic-cli /app/app/static/cli/lic-cli-linux-x64
    check_binary /app/app/static/cli/lic-cli-linux-x64
    
    cp -f target/x86_64-apple-darwin/release/lic-cli /app/app/static/cli/lic-cli-macos-x64
    check_binary /app/app/static/cli/lic-cli-macos-x64
    
    cp -f target/aarch64-apple-darwin/release/lic-cli /app/app/static/cli/lic-cli-macos-arm64
    check_binary /app/app/static/cli/lic-cli-macos-arm64
    
    echo "Build complete, sleeping for 1 hour..."
    sleep 3600
done
