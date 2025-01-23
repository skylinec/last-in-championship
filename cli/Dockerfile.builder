FROM rust:latest as builder

# Set default toolchain to stable to avoid pinned version issues
RUN rustup default stable

# Install cross-compilation tools
RUN apt-get update && apt-get install -y \
    gcc-mingw-w64-x86-64 \
    musl-tools \
    libssl-dev \
    && rustup target add x86_64-pc-windows-gnu \
    && rustup target add x86_64-unknown-linux-gnu

# Set up workspace
WORKDIR /usr/src/lic-cli
COPY . .

# Set PKG_CONFIG for cross-compilation
ENV PKG_CONFIG_ALLOW_CROSS=1 \
    OPENSSL_STATIC=1 \
    OPENSSL_DIR=/usr/lib/x86_64-linux-gnu

# Build for different platforms
RUN cargo build --release --target x86_64-pc-windows-gnu && \
    cargo build --release --target x86_64-unknown-linux-gnu 

# Copy binaries to output directory
RUN mkdir -p /output && \
    cp target/x86_64-pc-windows-gnu/release/lic-cli.exe /output/lic-cli-windows-x64.exe && \
    cp target/x86_64-unknown-linux-gnu/release/lic-cli /output/lic-cli-linux-x64
