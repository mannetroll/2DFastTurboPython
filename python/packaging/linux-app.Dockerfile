FROM python:3.13-slim-bookworm

# Freeze against glibc 2.36, with Qt's xcb dependencies available for collection.
# Driver libraries are excluded by linux-app.spec and supplied by the user.
RUN apt-get update && apt-get install -y --no-install-recommends \
    binutils libgl1 libegl1 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 \
    libxcb-keysyms1 libxcb-shape0 libxcb-xinerama0 libxcb-randr0 libxcb-render-util0 \
    libdbus-1-3 libfontconfig1 libfreetype6 libx11-xcb1 libglib2.0-0 libgssapi-krb5-2 \
    && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --no-cache-dir 'pyinstaller>=6.16,<7' 'PySide6==6.11.2'
WORKDIR /build/python
COPY scripts/bundle_entry.py scripts/package_linux.py scripts/
COPY packaging/linux-app.spec packaging/
ENTRYPOINT ["/bin/sh", "-c", "python -m pip install --no-deps /input/*.whl && python scripts/package_linux.py && cp dist/*.tar.gz /output/"]
