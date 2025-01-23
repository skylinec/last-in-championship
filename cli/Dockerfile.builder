FROM rust:latest as builder

# Install cross-compilation tools
RUN apt-get update && apt-get install -y \
    gcc-mingw-w64-x86-64 \
    gcc-aarch64-linux-gnu \
    musl-tools \
    libssl-dev \
    && rustup target add x86_64-pc-windows-gnu \
    && rustup target add x86_64-unknown-linux-gnu \
    # && rustup target add aarch64-unknown-linux-gnu

WORKDIR /usr/src/lic-cli
COPY . .

# Build for different platforms
RUN cargo build --release --target x86_64-pc-windows-gnu && \
    cargo build --release --target x86_64-unknown-linux-gnu && \
    # cargo build --release --target aarch64-unknown-linux-gnu

# Copy binaries to output directory
RUN mkdir -p /output && \
    cp target/x86_64-pc-windows-gnu/release/lic-cli.exe /output/lic-cli-windows-x64.exe && \
    cp target/x86_64-unknown-linux-gnu/release/lic-cli /output/lic-cli-linux-x64 && \
    # cp target/aarch64-unknown-linux-gnu/release/lic-cli /output/lic-cli-linux-arm64
