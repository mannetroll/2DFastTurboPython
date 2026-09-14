#pragma once
/* Version 1 opaque C ABI. JSON UTF-8 requests/results; caller frees returned strings.
   Initialize once on the main thread with the packaged metallib directory.
   Engine calls may use workers; serialize each handle with its destruction.
   UI calls and NSView attach/destroy require the macOS main thread. */
#ifdef __cplusplus
extern "C" {
#endif
char *ft_initialize(const char *resource_directory);
void ft_string_free(char *);
void *ft_engine_create(const char *configuration_json, char **error_out);
void ft_engine_destroy(void *engine);
char *ft_engine_call(void *engine, const char *request_json);
void *ft_ui_create(void);
void ft_ui_destroy(void *ui);
char *ft_ui_attach(void *ui, void *parent_nsview);
char *ft_ui_call(void *ui, const char *request_json);
#ifdef __cplusplus
}
#endif
