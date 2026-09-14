#pragma once
/* Version 1 opaque C ABI. JSON UTF-8 requests/results; caller frees returned strings.
   Initialize once on the main thread with the packaged resources directory.
   Engine calls may use workers; serialize each handle with its destruction.
   UI calls and native view attach/destroy require the main thread.
   Parent view is NSView on macOS, HWND on Windows. */
#if defined(_WIN32) && defined(FASTTURBO_EXPORTS)
#define FT_API __declspec(dllexport)
#else
#define FT_API
#endif
#ifdef __cplusplus
extern "C" {
#endif
FT_API char *ft_initialize(const char *resource_directory);
FT_API void ft_string_free(char *);
FT_API void *ft_engine_create(const char *configuration_json, char **error_out);
FT_API void ft_engine_destroy(void *engine);
FT_API char *ft_engine_call(void *engine, const char *request_json);
FT_API void *ft_ui_create(void);
FT_API void ft_ui_destroy(void *ui);
FT_API char *ft_ui_attach(void *ui, void *parent_view);
FT_API char *ft_ui_call(void *ui, const char *request_json);
#ifdef __cplusplus
}
#endif
