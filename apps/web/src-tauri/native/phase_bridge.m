#import "phase_bridge.h"

#import <AVFAudio/AVFAudio.h>
#import <Foundation/Foundation.h>
#import <PHASE/PHASE.h>
#import <simd/simd.h>

static const AVAudioFrameCount BUFFER_FRAMES = 512;
static const NSUInteger BUFFER_COUNT = 4;

@interface UpmixerPhase : NSObject
@property(nonatomic, strong) PHASEEngine *engine;
@property(nonatomic, strong) PHASESoundEvent *event;
@property(nonatomic, strong) PHASEPushStreamNode *stream;
@property(nonatomic, strong) PHASEListener *listener;
@property(nonatomic, strong) AVAudioFormat *format;
@property(nonatomic, strong) NSMutableArray<AVAudioPCMBuffer *> *available;
@property(nonatomic) dispatch_semaphore_t semaphore;
@property(nonatomic) BOOL upmixer714;
@end

@implementation UpmixerPhase
@end

static void set_error(char **out, NSError *error, NSString *fallback) {
    if (!out) return;
    NSString *message = error.localizedDescription ?: fallback;
    *out = strdup(message.UTF8String);
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
    [host.event pause];
}

void upmixer_phase_resume(UpmixerPhaseHost opaque) {
    UpmixerPhase *host = (__bridge UpmixerPhase *)opaque;
    [host.event resume];
}

bool upmixer_phase_schedule(UpmixerPhaseHost opaque, const float *const *channels,
                            uint32_t channel_count, uint32_t frames, char **error) {
    UpmixerPhase *host = (__bridge UpmixerPhase *)opaque;
    if (!host || channel_count != host.format.channelCount || frames > BUFFER_FRAMES) {
        set_error(error, nil, @"PHASE buffer format mismatch");
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
        uint32_t source = channel;
        if (host.upmixer714 && channel >= 4 && channel < 8) {
            source = channel < 6 ? channel + 2 : channel - 2;
        }
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
