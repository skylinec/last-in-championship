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

# Copy build script and make it executable
COPY build-loop.sh /build-loop.sh
RUN chmod +x /build-loop.sh

# Set PKG_CONFIG for cross-compilation
ENV PKG_CONFIG_ALLOW_CROSS=1 \
    OPENSSL_STATIC=1 \
    OPENSSL_DIR=/usr \
    OPENSSL_LIB_DIR=/usr/lib/x86_64-linux-gnu \
    OPENSSL_INCLUDE_DIR=/usr/include

# Create output directories and set permissions
RUN mkdir -p /app/static/cli && \
    chmod -R 755 /app/static/cli && \
    chown -R 1000:1000 /app/static/cli  # Standard user ID in most containers

# Keep container running with build loop
CMD ["/build-loop.sh"]
