/* Copyright (c) 2026 OceanBase. Licensed under the Apache License, Version 2.0. */

#import <AppKit/AppKit.h>
#import <Sparkle/Sparkle.h>

static SPUStandardUpdaterController *powercontextUpdaterController = nil;

void powercontext_sparkle_start(void) {
    NSCAssert([NSThread isMainThread], @"Sparkle must start on the AppKit main thread");
    if (powercontextUpdaterController != nil) {
        return;
    }
    @autoreleasepool {
        powercontextUpdaterController = [[SPUStandardUpdaterController alloc]
            initWithStartingUpdater:YES
            updaterDelegate:nil
            userDriverDelegate:nil];
    }
}

void powercontext_sparkle_check_for_updates(void) {
    NSCAssert([NSThread isMainThread], @"Sparkle update checks must run on the AppKit main thread");
    @autoreleasepool {
        [powercontextUpdaterController checkForUpdates:nil];
    }
}
