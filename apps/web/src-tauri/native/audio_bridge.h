#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef void *UpmixerAudioHost;

bool upmixer_audio_uses_media_pipeline(const char *layout, bool spatial);
UpmixerAudioHost upmixer_audio_create(const char *layout, bool spatial, char **error);
bool upmixer_audio_start(UpmixerAudioHost host, char **error);
void upmixer_audio_pause(UpmixerAudioHost host);
void upmixer_audio_resume(UpmixerAudioHost host);
bool upmixer_audio_schedule(UpmixerAudioHost host, const float *const *channels,
                            uint32_t channel_count, uint32_t frames, char **error);
void upmixer_audio_destroy(UpmixerAudioHost host);
void upmixer_audio_free_error(char *error);
uint32_t upmixer_audio_max_output_channels(void);
