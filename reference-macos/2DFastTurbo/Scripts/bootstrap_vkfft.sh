#!/bin/sh
set -eu

version="1.3.4"
project_root="${SRCROOT:-$(pwd)}"
target="${project_root}/ThirdParty/VkFFTSource"
header="${target}/vkFFT/vkFFT.h"
ready_stamp="${target}/.metal2dturbo-vkfft-${version}-ready"
ownership_patch="${project_root}/ThirdParty/Patches/vkfft-1.3.4-metal-appkit-ownership.patch"
ownership_marker="METAL2DTURBO_APPKIT_OWNERSHIP"

apply_metal_ownership_patch() {
    initialize_header="${target}/vkFFT/vkFFT/vkFFT_AppManagement/vkFFT_InitializeApp.h"

    if /usr/bin/grep -q "${ownership_marker}" "${initialize_header}"; then
        return
    fi
    if [ ! -f "${ownership_patch}" ]; then
        echo "error: missing VkFFT Metal ownership patch: ${ownership_patch}" >&2
        exit 1
    fi

    echo "Applying the Metal/AppKit ownership compatibility patch…"
    /usr/bin/patch -N -p1 -d "${target}" < "${ownership_patch}"
}

mark_dependency_ready() {
    /usr/bin/touch "${header}" "${ready_stamp}"
}

if [ -f "${header}" ]; then
    apply_metal_ownership_patch
    mark_dependency_ready
    exit 0
fi

archive_url="https://github.com/DTolm/VkFFT/archive/refs/tags/v${version}.zip"
temporary="$(mktemp -d "${TMPDIR:-/tmp}/metal2dturbo-vkfft.XXXXXX")"
trap 'rm -rf "${temporary}"' EXIT INT TERM

echo "Fetching VkFFT ${version} from the official repository…"
if ! /usr/bin/curl --fail --location --silent --show-error \
    "${archive_url}" --output "${temporary}/vkfft.zip"; then
    echo "warning: VkFFT download was unavailable; the built-in diagnostic FFT fallback will be used."
    exit 0
fi

/usr/bin/unzip -q "${temporary}/vkfft.zip" -d "${temporary}/source"
/bin/mkdir -p "$(dirname "${target}")"
/bin/rm -rf "${target}"
/bin/mv "${temporary}/source/VkFFT-${version}" "${target}"
apply_metal_ownership_patch
mark_dependency_ready
echo "VkFFT ${version} installed at ${target}"
