FROM rust:latest as builder

# Install cross-compilation tools and dependencies
RUN apt-get update && apt-get install -y \
    gcc-mingw-w64-x86-64 \
    gcc-aarch64-linux-gnu \
    musl-tools \
    libssl-dev \
    pkg-config \
    mingw-w64-tools \
    && rustup target add x86_64-pc-windows-gnu \
    && rustup target add x86_64-unknown-linux-musl \
    && rustup target add aarch64-unknown-linux-gnu

# Install pkg-config wrapper for cross-compilation
RUN echo '#!/bin/sh' > /usr/local/bin/aarch64-linux-gnu-pkg-config \
    && echo 'export PKG_CONFIG_PATH=/usr/lib/aarch64-linux-gnu/pkgconfig' >> /usr/local/bin/aarch64-linux-gnu-pkg-config \
    && echo 'exec pkg-config "$@"' >> /usr/local/bin/aarch64-linux-gnu-pkg-config \
    && chmod +x /usr/local/bin/aarch64-linux-gnu-pkg-config

# Set environment variables for cross-compilation
ENV OPENSSL_DIR=/usr/local/ssl \
    PKG_CONFIG_ALLOW_CROSS=1 \
    OPENSSL_STATIC=1 \
    OPENSSL_INCLUDE_DIR=/usr/include \
    OPENSSL_LIB_DIR=/usr/lib/x86_64-linux-gnu

WORKDIR /usr/src/lic-cli
COPY . .

# Build for Windows (x64)
RUN OPENSSL_LIB_DIR=/usr/lib/x86_64-linux-gnu \
    OPENSSL_INCLUDE_DIR=/usr/include \
    cargo build --release --target x86_64-pc-windows-gnu

# Build for Linux (x64)
RUN OPENSSL_LIB_DIR=/usr/lib/x86_64-linux-gnu \
    cargo build --release --target x86_64-unknown-linux-musl

# Build for Linux (ARM64)
RUN PKG_CONFIG=/usr/local/bin/aarch64-linux-gnu-pkg-config \
    CC=aarch64-linux-gnu-gcc \
    cargo build --release --target aarch64-unknown-linux-gnu

# Copy binaries to output directory
RUN mkdir -p /output && \
    cp target/x86_64-pc-windows-gnu/release/lic-cli.exe /output/lic-cli-windows-x64.exe && \
    cp target/x86_64-unknown-linux-musl/release/lic-cli /output/lic-cli-linux-x64 && \
    cp target/aarch64-unknown-linux-gnu/release/lic-cli /output/lic-cli-linux-arm64
