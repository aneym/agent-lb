APP      := AgentLB.app
BIN      := .build/release/AgentLB
BUNDLE_ID:= com.aneyman.agentlb.menubar
APP_PATH := $(HOME)/Applications/AgentLB.app
PLIST    := $(HOME)/Library/LaunchAgents/$(BUNDLE_ID).plist

.PHONY: build bundle run clean install deploy install-agent uninstall

build:
	swift build -c release

bundle: build
	rm -rf $(APP)
	mkdir -p $(APP)/Contents/MacOS $(APP)/Contents/Resources
	cp $(BIN) $(APP)/Contents/MacOS/AgentLB
	cp Resources/Info.plist $(APP)/Contents/Info.plist
	/usr/libexec/PlistBuddy -c "Add :AgentLBSourceRevision string $$(git rev-parse HEAD)" $(APP)/Contents/Info.plist
	codesign --force --deep --sign - $(APP)

run: bundle
	open $(APP)

install: bundle deploy install-agent

deploy:
	mkdir -p $(dir $(APP_PATH))
	rm -rf $(APP_PATH)
	ditto $(APP) $(APP_PATH)
	/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f $(APP_PATH)

install-agent:
	mkdir -p $(HOME)/Library/LaunchAgents
	printf '%s\n' \
	  '<?xml version="1.0" encoding="UTF-8"?>' \
	  '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">' \
	  '<plist version="1.0"><dict>' \
	  '  <key>Label</key><string>$(BUNDLE_ID)</string>' \
	  '  <key>ProgramArguments</key><array>' \
	  '    <string>/bin/sh</string>' \
	  '    <string>-c</string>' \
	  '    <string>i=0; until pgrep -xq ControlCenter || [ $$i -ge 90 ]; do sleep 1; i=$$((i+1)); done; sleep 3; exec "$$0"</string>' \
	  '    <string>$(APP_PATH)/Contents/MacOS/AgentLB</string>' \
	  '  </array>' \
	  '  <key>RunAtLoad</key><true/>' \
	  '  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>' \
	  '  <key>ThrottleInterval</key><integer>10</integer>' \
	  '  <key>ProcessType</key><string>Interactive</string>' \
	  '</dict></plist>' > $(PLIST)
	-launchctl bootout gui/$$(id -u)/$(BUNDLE_ID) 2>/dev/null
	launchctl bootstrap gui/$$(id -u) $(PLIST)

uninstall:
	-launchctl bootout gui/$$(id -u)/$(BUNDLE_ID) 2>/dev/null
	rm -f $(PLIST)

clean:
	swift package clean
	rm -rf $(APP)
