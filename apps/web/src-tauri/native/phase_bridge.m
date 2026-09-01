#import "phase_bridge.h"

#import <AVFoundation/AVFoundation.h>
#import <AVFAudio/AVFAudio.h>
#import <CoreMedia/CoreMedia.h>
#import <Foundation/Foundation.h>
#import <PHASE/PHASE.h>
#import <simd/simd.h>
#import <time.h>
#import <unistd.h>

static const AVAudioFrameCount BUFFER_FRAMES = 512;
static const NSUInteger BUFFER_COUNT = 4;

@interface UpmixerPhase : NSObject
@property(nonatomic, strong) PHASEEngine *engine;
@property(nonatomic, strong) PHASESoundEvent *event;
@property(nonatomic, strong) PHASEPushStreamNode *stream;
@property(nonatomic, strong) PHASEListener *listener;
@property(nonatomic, strong) AVSampleBufferAudioRenderer *mediaRenderer;
@property(nonatomic, strong) AVSampleBufferRenderSynchronizer *mediaSynchronizer;
@property(nonatomic, strong) AVAudioFormat *format;
@property(nonatomic, strong) NSMutableArray<AVAudioPCMBuffer *> *available;
@property(nonatomic) dispatch_semaphore_t semaphore;
@property(nonatomic) BOOL mediaPipeline;
@property(nonatomic) BOOL mediaPlaying;
@property(nonatomic) BOOL mediaStarted;
@property(nonatomic) int64_t nextFrame;
@property(nonatomic) BOOL upmixer714;
@end

@implementation UpmixerPhase
@end

static void set_error(char **out, NSError *error, NSString *fallback) {
    if (!out) return;
    NSString *message = error.localizedDescription ?: fallback;
    *out = strdup(message.UTF8String);
}

static bool set_status_error(char **out, OSStatus status, NSString *fallback) {
    NSString *message = [NSString stringWithFormat:@"%@ (OSStatus %d)", fallback, (int)status];
    set_error(out, nil, message);
    return false;
}

static double monotonic_seconds(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return now.tv_sec + now.tv_nsec / 1000000000.0;
}

static AudioChannelLayoutTag layout_tag(NSString *layout) {
    if ([layout isEqualToString:@"5.1.2"]) return kAudioChannelLayoutTag_Atmos_5_1_2;
    if ([layout isEqualToString:@"5.1.4"]) return kAudioChannelLayoutTag_Atmos_5_1_4;
    if ([layout isEqualToString:@"7.1.2"]) return kAudioChannelLayoutTag_Atmos_7_1_2;
    if ([layout isEqualToString:@"7.1.4"]) return kAudioChannelLayoutTag_Atmos_7_1_4;
    if ([layout isEqualToString:@"5.1"]) return kAudioChannelLayoutTag_MPEG_5_1_A;
    if ([layout isEqualToString:@"7.1"]) return kAudioChannelLayoutTag_MPEG_7_1_A;
    return kAudioChannelLayoutTag_Stereo;
}

bool upmixer_phase_uses_media_pipeline(const char *layout_name, bool spatial) {
    if (!spatial) return false;
    @autoreleasepool {
        NSString *layout = [NSString stringWithUTF8String:layout_name ?: "stereo"];
        return ![layout isEqualToString:@"stereo"];
    }
}

UpmixerPhaseHost upmixer_phase_create(const char *layout_name, bool spatial, char **error) {
    @autoreleasepool {
        NSString *layoutName = [NSString stringWithUTF8String:layout_name ?: "stereo"];
        AVAudioChannelLayout *layout = [[AVAudioChannelLayout alloc] initWithLayoutTag:layout_tag(layoutName)];
        if (!layout) {
            set_error(error, nil, @"Unsupported output channel layout");
            return NULL;
        }

        UpmixerPhase *host = [UpmixerPhase new];
        host.upmixer714 = [layoutName isEqualToString:@"7.1.4"];
        host.mediaPipeline = upmixer_phase_uses_media_pipeline(layout_name, spatial);
        if (host.mediaPipeline) {
            host.format = [[AVAudioFormat alloc] initWithCommonFormat:AVAudioPCMFormatFloat32
                                                          sampleRate:48000
                                                         interleaved:YES
                                                       channelLayout:layout];
            if (!host.format) {
                set_error(error, nil, @"Could not create the Apple media audio format");
                return NULL;
            }
            host.mediaRenderer = [AVSampleBufferAudioRenderer new];
            host.mediaRenderer.allowedAudioSpatializationFormats = AVAudioSpatializationFormatMultichannel;
            host.mediaSynchronizer = [AVSampleBufferRenderSynchronizer new];
            [host.mediaSynchronizer addRenderer:host.mediaRenderer];
            return (__bridge_retained void *)host;
        }

        host.engine = [[PHASEEngine alloc] initWithUpdateMode:PHASEUpdateModeAutomatic];
        host.engine.outputSpatializationMode = PHASESpatializationModeAutomatic;
        host.format = [[AVAudioFormat alloc] initStandardFormatWithSampleRate:48000 channelLayout:layout];

        PHASEMixerDefinition *mixer;
        NSString *mixerIdentifier;
        if (spatial) {
            mixerIdentifier = @"upmixer-ambient";
            mixer = [[PHASEAmbientMixerDefinition alloc]
                initWithChannelLayout:layout
                orientation:simd_quaternion(0.0f, 0.0f, 0.0f, 1.0f)
                identifier:mixerIdentifier];
        } else {
            mixerIdentifier = @"upmixer-direct";
            mixer = [[PHASEChannelMixerDefinition alloc] initWithChannelLayout:layout identifier:mixerIdentifier];
        }

        PHASEPushStreamNodeDefinition *streamDefinition = [[PHASEPushStreamNodeDefinition alloc]
            initWithMixerDefinition:mixer format:host.format identifier:@"upmixer-stream"];
        streamDefinition.normalize = NO;
        NSError *phaseError = nil;
        [host.engine.assetRegistry registerSoundEventAssetWithRootNode:streamDefinition
                                                            identifier:@"upmixer-event"
                                                                 error:&phaseError];
        if (phaseError) {
            set_error(error, phaseError, @"Could not register the PHASE stream");
            return NULL;
        }

        PHASEMixerParameters *parameters = [PHASEMixerParameters new];
        if (spatial) {
            host.listener = [[PHASEListener alloc] initWithEngine:host.engine];
            if (![host.engine.rootObject addChild:host.listener error:&phaseError]) {
                set_error(error, phaseError, @"Could not attach the PHASE listener");
                return NULL;
            }
            [parameters addAmbientMixerParametersWithIdentifier:mixerIdentifier listener:host.listener];
        }

        host.event = [[PHASESoundEvent alloc] initWithEngine:host.engine
                                            assetIdentifier:@"upmixer-event"
                                             mixerParameters:parameters
                                                       error:&phaseError];
        if (!host.event) {
            set_error(error, phaseError, @"Could not create the PHASE sound event");
            return NULL;
        }
        host.stream = host.event.pushStreamNodes[@"upmixer-stream"];
        host.available = [NSMutableArray arrayWithCapacity:BUFFER_COUNT];
        host.semaphore = dispatch_semaphore_create(BUFFER_COUNT);
        for (NSUInteger i = 0; i < BUFFER_COUNT; i++) {
            AVAudioPCMBuffer *buffer = [[AVAudioPCMBuffer alloc] initWithPCMFormat:host.format frameCapacity:BUFFER_FRAMES];
            [host.available addObject:buffer];
        }
        return (__bridge_retained void *)host;
    }
}

bool upmixer_phase_start(UpmixerPhaseHost opaque, char **error) {
    @autoreleasepool {
        UpmixerPhase *host = (__bridge UpmixerPhase *)opaque;
        if (host.mediaPipeline) return true;
        NSError *phaseError = nil;
        if (![host.engine startAndReturnError:&phaseError]) {
            set_error(error, phaseError, @"Could not start PHASE");
            return false;
        }
        [host.event startWithCompletion:nil];
        host.stream = host.event.pushStreamNodes[@"upmixer-stream"];
        if (!host.stream) {
            set_error(error, nil, @"PHASE did not create the push stream");
            return false;
        }
        return true;
    }
}

void upmixer_phase_pause(UpmixerPhaseHost opaque) {
    UpmixerPhase *host = (__bridge UpmixerPhase *)opaque;
    if (host.mediaPipeline) {
        host.mediaPlaying = NO;
        host.mediaSynchronizer.rate = 0.0f;
        return;
    }
    [host.event pause];
}

void upmixer_phase_resume(UpmixerPhaseHost opaque) {
    UpmixerPhase *host = (__bridge UpmixerPhase *)opaque;
    if (host.mediaPipeline) {
        host.mediaPlaying = YES;
        if (host.mediaStarted) host.mediaSynchronizer.rate = 1.0f;
        return;
    }
    [host.event resume];
}

static bool media_renderer_failed(UpmixerPhase *host, char **error) {
    if (host.mediaRenderer.status != AVQueuedSampleBufferRenderingStatusFailed) return false;
    set_error(error, host.mediaRenderer.error, @"Apple media renderer failed");
    return true;
}

static bool wait_for_media_capacity(UpmixerPhase *host, char **error) {
    const double deadline = monotonic_seconds() + 2.0;
    while (true) {
        if (media_renderer_failed(host, error)) return false;
        if (host.mediaRenderer.readyForMoreMediaData) return true;
        if (monotonic_seconds() >= deadline) {
            set_error(error, nil, @"Apple media renderer stopped accepting audio");
            return false;
        }
        usleep(1000);
    }
}

static uint32_t source_channel(UpmixerPhase *host, uint32_t channel) {
    if (host.upmixer714 && channel >= 4 && channel < 8) {
        return channel < 6 ? channel + 2 : channel - 2;
    }
    return channel;
}

static bool schedule_media_buffer(UpmixerPhase *host, const float *const *channels,
                                  uint32_t channelCount, uint32_t frames, char **error) {
    @autoreleasepool {
        if (!wait_for_media_capacity(host, error)) return false;

        size_t byteCount = (size_t)frames * channelCount * sizeof(float);
        CMBlockBufferRef block = NULL;
        OSStatus status = CMBlockBufferCreateWithMemoryBlock(
            kCFAllocatorDefault, NULL, byteCount, kCFAllocatorDefault, NULL, 0, byteCount,
            kCMBlockBufferAssureMemoryNowFlag, &block);
        if (status != noErr) return set_status_error(error, status, @"Could not allocate an Apple media buffer");

        char *bytes = NULL;
        status = CMBlockBufferGetDataPointer(block, 0, NULL, NULL, &bytes);
        if (status != noErr) {
            CFRelease(block);
            return set_status_error(error, status, @"Could not access an Apple media buffer");
        }
        float *interleaved = (float *)bytes;
        for (uint32_t frame = 0; frame < frames; frame++) {
            for (uint32_t channel = 0; channel < channelCount; channel++) {
                interleaved[(size_t)frame * channelCount + channel] =
                    channels[source_channel(host, channel)][frame];
            }
        }

        CMSampleTimingInfo timing = {
            .duration = CMTimeMake(1, 48000),
            .presentationTimeStamp = CMTimeMake(host.nextFrame, 48000),
            .decodeTimeStamp = kCMTimeInvalid,
        };
        size_t sampleSize = channelCount * sizeof(float);
        CMSampleBufferRef sample = NULL;
        status = CMSampleBufferCreate(kCFAllocatorDefault, block, true, NULL, NULL,
                                      host.format.formatDescription, frames, 1, &timing,
                                      1, &sampleSize, &sample);
        CFRelease(block);
        if (status != noErr) return set_status_error(error, status, @"Could not create an Apple audio sample");

        [host.mediaRenderer enqueueSampleBuffer:sample];
        CFRelease(sample);
        host.nextFrame += frames;
        if (host.mediaPlaying && !host.mediaStarted) {
            host.mediaStarted = YES;
            [host.mediaSynchronizer setRate:1.0f time:kCMTimeZero];
        }
        return !media_renderer_failed(host, error);
    }
}

bool upmixer_phase_schedule(UpmixerPhaseHost opaque, const float *const *channels,
                            uint32_t channel_count, uint32_t frames, char **error) {
    UpmixerPhase *host = (__bridge UpmixerPhase *)opaque;
    if (!host || channel_count != host.format.channelCount || frames > BUFFER_FRAMES) {
        set_error(error, nil, @"Native audio buffer format mismatch");
        return false;
    }
    if (host.mediaPipeline) {
        return schedule_media_buffer(host, channels, channel_count, frames, error);
    }
    dispatch_semaphore_wait(host.semaphore, DISPATCH_TIME_FOREVER);
    __block AVAudioPCMBuffer *buffer;
    @synchronized(host.available) {
        buffer = host.available.lastObject;
        [host.available removeLastObject];
    }
    buffer.frameLength = frames;
    for (uint32_t channel = 0; channel < channel_count; channel++) {
        uint32_t source = source_channel(host, channel);
        memcpy(buffer.floatChannelData[channel], channels[source], frames * sizeof(float));
    }
    [host.stream scheduleBuffer:buffer
         completionCallbackType:PHASEPushStreamCompletionDataRendered
              completionHandler:^(PHASEPushStreamCompletionCallbackCondition condition) {
        (void)condition;
        @synchronized(host.available) {
            [host.available addObject:buffer];
        }
        dispatch_semaphore_signal(host.semaphore);
    }];
    return true;
}

void upmixer_phase_destroy(UpmixerPhaseHost opaque) {
    if (!opaque) return;
    UpmixerPhase *host = (__bridge_transfer UpmixerPhase *)opaque;
    if (host.mediaPipeline) {
        host.mediaSynchronizer.rate = 0.0f;
        [host.mediaRenderer flush];
        return;
    }
    [host.event stopAndInvalidate];
    [host.engine stop];
}

void upmixer_phase_free_error(char *error) {
    free(error);
}

uint32_t upmixer_phase_max_output_channels(void) {
    @autoreleasepool {
        AVAudioEngine *engine = [AVAudioEngine new];
        return [[engine.outputNode outputFormatForBus:0] channelCount];
    }
}
