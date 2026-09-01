#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef void *UpmixerPhaseHost;

UpmixerPhaseHost upmixer_phase_create(const char *layout, bool spatial, char **error);
bool upmixer_phase_start(UpmixerPhaseHost host, char **error);
void upmixer_phase_pause(UpmixerPhaseHost host);
void upmixer_phase_resume(UpmixerPhaseHost host);
bool upmixer_phase_schedule(UpmixerPhaseHost host, const float *const *channels,
                            uint32_t channel_count, uint32_t frames, char **error);
void upmixer_phase_destroy(UpmixerPhaseHost host);
void upmixer_phase_free_error(char *error);
uint32_t upmixer_phase_max_output_channels(void);
