FROM rust:latest as builder

# Set default toolchain to stable to avoid pinned version issues
RUN rustup default stable

# Install build dependencies
RUN apt-get update && apt-get install -y \
    gcc-mingw-w64-x86-64 \
    musl-tools \
    libssl-dev \
    clang \
    llvm \
    cmake \
    xz-utils \
    patch \
    libxml2 \
    git \
    curl \
    && rustup target add x86_64-pc-windows-gnu \
    && rustup target add x86_64-unknown-linux-gnu \
    && rustup target add x86_64-apple-darwin \
    && rustup target add aarch64-apple-darwin

# Set up OSX Cross compilation environment
WORKDIR /tmp
RUN git clone --depth 1 https://github.com/tpoechtrager/osxcross.git \
    && cd osxcross \
    && wget -nc https://github.com/joseluisq/macosx-sdks/releases/download/12.3/MacOSX12.3.sdk.tar.xz \
    && mv MacOSX12.3.sdk.tar.xz tarballs/ \
    && UNATTENDED=1 ./build.sh \
    && mkdir -p /usr/local/osx-ndk-x86 \
    && cp -r target/* /usr/local/osx-ndk-x86

# Add OSX Cross tools to PATH and set proper env vars
ENV PATH="/usr/local/osx-ndk-x86/bin:$PATH" \
    CC_x86_64_apple_darwin=o64-clang \
    CXX_x86_64_apple_darwin=o64-clang++ \
    AR_x86_64_apple_darwin=x86_64-apple-darwin20.4-ar \
    CC_aarch64_apple_darwin=aarch64-apple-darwin20.4-clang \
    CXX_aarch64_apple_darwin=aarch64-apple-darwin20.4-clang++ \
    AR_aarch64_apple_darwin=aarch64-apple-darwin20.4-ar \
    CARGO_TARGET_X86_64_APPLE_DARWIN_LINKER=o64-clang \
    CARGO_TARGET_AARCH64_APPLE_DARWIN_LINKER=aarch64-apple-darwin20.4-clang

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
    chown -R 1000:1000 /app/app/static/cli

# Keep container running with build loop
CMD ["/build-loop.sh"]
