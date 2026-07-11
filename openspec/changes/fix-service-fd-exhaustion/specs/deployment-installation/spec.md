# deployment-installation Delta

## ADDED Requirements

### Requirement: Service plist carries file-descriptor headroom by default

The generated LaunchAgent plist MUST set `SoftResourceLimits.NumberOfFiles`
to at least 4096 and `HardResourceLimits.NumberOfFiles` to at least 8192 when
the existing plist does not already define them, because launchd's default of
256 open files is exhausted by the proxy's keep-alive upstream connection
pools and manifests as 500 `server_error` responses ("Too many open files",
"unable to open database file") and DNS failures on proxy routes. Existing
resource-limit dictionaries in the plist MUST be preserved verbatim so
operator customizations survive reinstalls.

#### Scenario: Fresh install gets fd headroom

- **GIVEN** no existing LaunchAgent plist
- **WHEN** the installer generates the plist
- **THEN** it contains `SoftResourceLimits.NumberOfFiles = 4096` and
  `HardResourceLimits.NumberOfFiles = 8192`

#### Scenario: Customized limits survive reinstall

- **GIVEN** an existing plist with `SoftResourceLimits.NumberOfFiles = 2048`
  and a `HardResourceLimits` dictionary containing additional keys
- **WHEN** the installer regenerates the plist
- **THEN** the customized limit values and additional keys are preserved
  verbatim
