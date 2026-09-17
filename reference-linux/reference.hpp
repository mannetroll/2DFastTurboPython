#pragma once
#include "solver.hpp"
#include "legacy_cnab2.hpp"
#include <filesystem>
#include <json.hpp>
using Json = nlohmann::ordered_json;
namespace reference {
Json statistics(const turbo::Statistics& s);
Json validate(const std::filesystem::path& root);
Json compareSpectrum(const std::vector<turbo::Complex>& actual,const std::filesystem::path& path);
}
