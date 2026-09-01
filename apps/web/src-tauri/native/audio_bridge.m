#import "audio_bridge.h"

#import <AVFAudio/AVFAudio.h>
#import <Foundation/Foundation.h>
#import <PHASE/PHASE.h>
#import <time.h>
#import <unistd.h>

static const AVAudioFrameCount BUFFER_FRAMES = 512;
static const NSUInteger BUFFER_COUNT = 4;
static const uint32_t LFE_CHANNEL = 3;
static const float LFE_FRONT_FOLD_GAIN = 1.58113883f;

@interface UpmixerAudio : NSObject
@property(nonatomic, strong) AVAudioEngine *engine;
@property(nonatomic, strong) AVAudioPlayerNode *player;
@property(nonatomic, strong) PHASEEngine *phaseEngine;
@property(nonatomic, strong) PHASESoundEvent *phaseEvent;
@property(nonatomic, strong) PHASEPushStreamNode *phaseStream;
@property(nonatomic, strong) PHASEListener *listener;
@property(nonatomic, strong) AVAudioFormat *format;
@property(nonatomic, strong) NSMutableArray<AVAudioPCMBuffer *> *available;
@property(nonatomic) dispatch_semaphore_t semaphore;
@property(nonatomic) BOOL phasePipeline;
@property(nonatomic) int64_t nextFrame;
@property(nonatomic) int64_t presentedFrame;
@property(nonatomic) NSTimeInterval presentationLatency;
@property(nonatomic) BOOL upmixer714;
@end

@implementation UpmixerAudio
@end

static void set_error(char **out, NSError *error, NSString *fallback) {
    if (!out) return;
    NSString *message = error.localizedDescription ?: fallback;
    *out = strdup(message.UTF8String);
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

bool upmixer_audio_uses_media_pipeline(const char *layout_name, bool spatial) {
    if (!spatial) return false;
    @autoreleasepool {
        NSString *layout = [NSString stringWithUTF8String:layout_name ?: "stereo"];
        return ![layout isEqualToString:@"stereo"];
    }
}

UpmixerAudioHost upmixer_audio_create(const char *layout_name, bool spatial, bool head_tracking,
                                      int64_t start_frame,
                                      char **error) {
    @autoreleasepool {
        NSString *layoutName = [NSString stringWithUTF8String:layout_name ?: "stereo"];
        AVAudioChannelLayout *layout = [[AVAudioChannelLayout alloc] initWithLayoutTag:layout_tag(layoutName)];
        if (!layout) {
            set_error(error, nil, @"Unsupported output channel layout");
            return NULL;
        }

        UpmixerAudio *host = [UpmixerAudio new];
        host.upmixer714 = [layoutName isEqualToString:@"7.1.4"];
        host.phasePipeline = upmixer_audio_uses_media_pipeline(layout_name, spatial);
        host.nextFrame = start_frame;
        host.presentedFrame = start_frame;
        if (host.phasePipeline) {
            host.format = [[AVAudioFormat alloc] initWithCommonFormat:AVAudioPCMFormatFloat32
                                                          sampleRate:48000
                                                         interleaved:NO
                                                       channelLayout:layout];
            if (!host.format) {
                set_error(error, nil, @"Could not create the PHASE audio format");
                return NULL;
            }
            host.phaseEngine = [[PHASEEngine alloc] initWithUpdateMode:PHASEUpdateModeAutomatic];
            host.phaseEngine.outputSpatializationMode = PHASESpatializationModeAutomatic;
            AVAudioEngine *latencyEngine = [AVAudioEngine new];
            AVAudioOutputNode *output = latencyEngine.outputNode;
            // ponytail: PHASE exposes no spatializer latency; include it if Apple adds a presentation clock.
            host.presentationLatency = output.presentationLatency + output.latency;
            PHASEListener *listener = [[PHASEListener alloc] initWithEngine:host.phaseEngine];
            listener.automaticHeadTrackingFlags = head_tracking ? PHASEAutomaticHeadTrackingFlagOrientation : 0;
            NSError *phaseError = nil;
            if (![host.phaseEngine.rootObject addChild:listener error:&phaseError]) {
                set_error(error, phaseError, @"Could not attach the PHASE listener");
                return NULL;
            }
            host.listener = listener;
            PHASEAmbientMixerDefinition *mixer =
                [[PHASEAmbientMixerDefinition alloc] initWithChannelLayout:layout
                                                                 orientation:simd_quaternion(0.0f, (simd_float3){0.0f, 0.0f, 1.0f})
                                                                  identifier:@"upmixer_ambient_mixer"];
            PHASEPushStreamNodeDefinition *streamDefinition =
                [[PHASEPushStreamNodeDefinition alloc] initWithMixerDefinition:mixer
                                                                         format:host.format
                                                                    identifier:@"upmixer_push_stream"];
            streamDefinition.normalize = NO;
            PHASESoundEventNodeAsset *asset =
                [host.phaseEngine.assetRegistry registerSoundEventAssetWithRootNode:streamDefinition
                                                                          identifier:@"upmixer_sound_event"
                                                                               error:&phaseError];
            if (!asset) {
                set_error(error, phaseError, @"Could not register the PHASE sound event");
                return NULL;
            }
            PHASEMixerParameters *parameters = [PHASEMixerParameters new];
            [parameters addAmbientMixerParametersWithIdentifier:mixer.identifier listener:listener];
            host.phaseEvent = [[PHASESoundEvent alloc] initWithEngine:host.phaseEngine
                                                        assetIdentifier:asset.identifier
                                                        mixerParameters:parameters
                                                                  error:&phaseError];
            if (!host.phaseEvent) {
                set_error(error, phaseError, @"Could not create the PHASE sound event");
                return NULL;
            }
        } else {
            host.format = [[AVAudioFormat alloc] initStandardFormatWithSampleRate:48000 channelLayout:layout];
            host.engine = [AVAudioEngine new];
            host.player = [AVAudioPlayerNode new];
            [host.engine attachNode:host.player];
            [host.engine connect:host.player to:host.engine.outputNode format:host.format];
        }
        host.available = [NSMutableArray arrayWithCapacity:BUFFER_COUNT];
        host.semaphore = dispatch_semaphore_create(BUFFER_COUNT);
        for (NSUInteger i = 0; i < BUFFER_COUNT; i++) {
            AVAudioPCMBuffer *buffer = [[AVAudioPCMBuffer alloc] initWithPCMFormat:host.format frameCapacity:BUFFER_FRAMES];
            [host.available addObject:buffer];
        }
        return (__bridge_retained void *)host;
    }
}

void upmixer_audio_set_head_tracking(UpmixerAudioHost opaque, bool enabled) {
    UpmixerAudio *host = (__bridge UpmixerAudio *)opaque;
    if (host.phasePipeline) {
        host.listener.automaticHeadTrackingFlags = enabled ? PHASEAutomaticHeadTrackingFlagOrientation : 0;
    }
}

bool upmixer_audio_start(UpmixerAudioHost opaque, char **error) {
    @autoreleasepool {
        UpmixerAudio *host = (__bridge UpmixerAudio *)opaque;
        if (host.phasePipeline) {
            NSError *engineError = nil;
            if (![host.phaseEngine startAndReturnError:&engineError]) {
                set_error(error, engineError, @"Could not start PHASE audio output");
                return false;
            }
            [host.phaseEvent startWithCompletion:nil];
            double deadline = monotonic_seconds() + 2.0;
            while (!host.phaseStream) {
                host.phaseStream = host.phaseEvent.pushStreamNodes[@"upmixer_push_stream"];
                if (monotonic_seconds() >= deadline) {
                    set_error(error, nil, @"PHASE did not start the push stream");
                    return false;
                }
                usleep(1000);
            }
            return true;
        }
        NSError *engineError = nil;
        if (![host.engine startAndReturnError:&engineError]) {
            set_error(error, engineError, @"Could not start direct audio output");
            return false;
        }
        return true;
    }
}

void upmixer_audio_pause(UpmixerAudioHost opaque) {
    UpmixerAudio *host = (__bridge UpmixerAudio *)opaque;
    if (host.phasePipeline) {
        [host.phaseEvent pause];
        return;
    }
    [host.player pause];
}

void upmixer_audio_resume(UpmixerAudioHost opaque) {
    UpmixerAudio *host = (__bridge UpmixerAudio *)opaque;
    if (host.phasePipeline) {
        [host.phaseEvent resume];
        return;
    }
    [host.player play];
}

static uint32_t source_channel(UpmixerAudio *host, uint32_t channel) {
    if (host.upmixer714 && channel >= 4 && channel < 8) {
        return channel < 6 ? channel + 2 : channel - 2;
    }
    return channel;
}

static void fold_lfe_into_fronts(AVAudioPCMBuffer *buffer, uint32_t frames) {
    float *left = buffer.floatChannelData[0];
    float *right = buffer.floatChannelData[1];
    float *lfe = buffer.floatChannelData[LFE_CHANNEL];
    for (uint32_t frame = 0; frame < frames; frame++) {
        float folded = lfe[frame] * LFE_FRONT_FOLD_GAIN;
        left[frame] += folded;
        right[frame] += folded;
        lfe[frame] = 0.0f;
    }
}

bool upmixer_audio_schedule(UpmixerAudioHost opaque, const float *const *channels,
                            uint32_t channel_count, uint32_t frames, char **error) {
    UpmixerAudio *host = (__bridge UpmixerAudio *)opaque;
    if (!host || channel_count != host.format.channelCount || frames > BUFFER_FRAMES) {
        set_error(error, nil, @"Native audio buffer format mismatch");
        return false;
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
    if (host.phasePipeline) {
        fold_lfe_into_fronts(buffer, frames);
        int64_t endFrame = host.nextFrame + frames;
        host.nextFrame = endFrame;
        [host.phaseStream scheduleBuffer:buffer
                   completionCallbackType:PHASEPushStreamCompletionDataRendered
                        completionHandler:^(PHASEPushStreamCompletionCallbackCondition condition) {
            (void)condition;
            @synchronized(host.available) {
                [host.available addObject:buffer];
            }
            dispatch_semaphore_signal(host.semaphore);
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW,
                                         (int64_t)(host.presentationLatency * NSEC_PER_SEC)),
                           dispatch_get_global_queue(QOS_CLASS_USER_INTERACTIVE, 0), ^{
                @synchronized(host) {
                    host.presentedFrame = MAX(host.presentedFrame, endFrame);
                }
            });
        }];
    } else {
        [host.player scheduleBuffer:buffer
             completionCallbackType:AVAudioPlayerNodeCompletionDataRendered
                  completionHandler:^(AVAudioPlayerNodeCompletionCallbackType condition) {
            (void)condition;
            @synchronized(host.available) {
                [host.available addObject:buffer];
            }
            dispatch_semaphore_signal(host.semaphore);
        }];
    }
    return true;
}

int64_t upmixer_audio_playback_frame(UpmixerAudioHost opaque) {
    UpmixerAudio *host = (__bridge UpmixerAudio *)opaque;
    if (!host.phasePipeline) return -1;
    @synchronized(host) {
        return host.presentedFrame;
    }
}

void upmixer_audio_destroy(UpmixerAudioHost opaque) {
    if (!opaque) return;
    UpmixerAudio *host = (__bridge_transfer UpmixerAudio *)opaque;
    if (host.phasePipeline) {
        [host.phaseEvent stopAndInvalidate];
        [host.phaseEngine stop];
        return;
    }
    [host.player stop];
    [host.engine stop];
}

void upmixer_audio_free_error(char *error) {
    free(error);
}

uint32_t upmixer_audio_max_output_channels(void) {
    @autoreleasepool {
        AVAudioEngine *engine = [AVAudioEngine new];
        return [[engine.outputNode outputFormatForBus:0] channelCount];
    }
}
