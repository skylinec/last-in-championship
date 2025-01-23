FROM rust:latest as builder

# Set default toolchain to stable to avoid pinned version issues
RUN rustup default stable

# Install cross-compilation tools
RUN apt-get update && apt-get install -y \
    gcc-mingw-w64-x86-64 \
    musl-tools \
    libssl-dev \
    clang \
    llvm \
    cmake \
    xz-utils \
    && rustup target add x86_64-pc-windows-gnu \
    && rustup target add x86_64-unknown-linux-gnu \
    && rustup target add x86_64-apple-darwin \
    && rustup target add aarch64-apple-darwin

# Install OSX Cross toolchain
RUN git clone https://github.com/tpoechtrager/osxcross \
    && cd osxcross \
    && wget -nc https://github.com/joseluisq/macosx-sdks/releases/download/12.3/MacOSX12.3.sdk.tar.xz \
    && mv MacOSX12.3.sdk.tar.xz tarballs/ \
    && UNATTENDED=1 ./build.sh \
    && rm -rf /osxcross/build

ENV PATH="/osxcross/target/bin:$PATH"

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
RUN mkdir -p /app/app/static/cli && \
    chmod -R 755 /app/app/static/cli && \
    chown -R 1000:1000 /app/app/static/cli  # Standard user ID in most containers

# Keep container running with build loop
CMD ["/build-loop.sh"]
