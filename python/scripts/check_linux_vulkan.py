"""Enumerate installed Vulkan devices and external-memory support without rendering."""
import ctypes as C
import json
from pathlib import Path


class ApplicationInfo(C.Structure):
    _fields_ = [("sType", C.c_uint32), ("pNext", C.c_void_p), ("name", C.c_char_p),
                ("version", C.c_uint32), ("engine", C.c_char_p), ("engineVersion", C.c_uint32),
                ("apiVersion", C.c_uint32)]


class InstanceInfo(C.Structure):
    _fields_ = [("sType", C.c_uint32), ("pNext", C.c_void_p), ("flags", C.c_uint32),
                ("application", C.POINTER(ApplicationInfo)), ("layerCount", C.c_uint32),
                ("layers", C.c_void_p), ("extensionCount", C.c_uint32), ("extensions", C.c_void_p)]


class Properties(C.Structure):
    # Oversized trailing storage receives VkPhysicalDeviceLimits/SparseProperties;
    # only Vulkan's stable version/vendor/name prefix is interpreted here.
    _fields_ = [("apiVersion", C.c_uint32), ("driverVersion", C.c_uint32),
                ("vendorID", C.c_uint32), ("deviceID", C.c_uint32), ("deviceType", C.c_uint32),
                ("deviceName", C.c_char * 256), ("remaining", C.c_uint64 * 512)]


class Extension(C.Structure):
    _fields_ = [("name", C.c_char * 256), ("version", C.c_uint32)]


def main():
    vk = C.CDLL("libvulkan.so.1")
    instance = C.c_void_p()
    app = ApplicationInfo(0, None, b"FastTurbo interop probe", 1, b"probe", 1, 1 << 22)
    info = InstanceInfo(1, None, 0, C.pointer(app), 0, None, 0, None)
    code = vk.vkCreateInstance(C.byref(info), None, C.byref(instance))
    report = {"vkCreateInstance": code, "devices": []}
    if code == 0:
        try:
            count = C.c_uint32()
            code = vk.vkEnumeratePhysicalDevices(instance, C.byref(count), None)
            report["vkEnumeratePhysicalDevices"] = code
            devices = (C.c_void_p * count.value)()
            if code == 0:
                code = vk.vkEnumeratePhysicalDevices(instance, C.byref(count), devices)
                if code != 0:
                    raise RuntimeError(f"Vulkan enumeration failed: {code}")
                for value in devices:
                    device = C.c_void_p(value)
                    properties = Properties()
                    vk.vkGetPhysicalDeviceProperties(device, C.byref(properties))
                    count = C.c_uint32()
                    vk.vkEnumerateDeviceExtensionProperties(device, None, C.byref(count), None)
                    extensions = (Extension * count.value)()
                    vk.vkEnumerateDeviceExtensionProperties(device, None, C.byref(count), extensions)
                    names = [x.name.decode() for x in extensions]
                    report["devices"].append({"name": properties.deviceName.decode(),
                        "vendor_id": properties.vendorID, "type": properties.deviceType,
                        "external_memory_extensions": [n for n in names if "external_memory" in n or "external_semaphore" in n]})
        finally:
            vk.vkDestroyInstance(instance, None)
    report["nvidia_gpu_available"] = any(d["vendor_id"] == 0x10DE and d["type"] != 4 for d in report["devices"])
    output = Path("evidence/linux/vulkan-interop.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["nvidia_gpu_available"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
