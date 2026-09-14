#pragma once
#include <windows.h>
#include <span>
#include <stdexcept>
namespace turbo {
inline std::span<const char> resource(int id) {
    auto module=GetModuleHandleW(nullptr);
    auto entry=FindResourceW(module,MAKEINTRESOURCEW(id),RT_RCDATA);
    if(!entry)throw std::runtime_error("Missing embedded application resource.");
    auto loaded=LoadResource(module,entry);
    auto data=static_cast<const char*>(LockResource(loaded));
    if(!data)throw std::runtime_error("Cannot load embedded application resource.");
    return {data,SizeofResource(module,entry)};
}
}
