#pragma once
// Select the active WSL driver's own JIT dependencies, when running under WSL.
// Ordinary Linux uses the system driver loader unchanged.
void initializeCudaLoader();
