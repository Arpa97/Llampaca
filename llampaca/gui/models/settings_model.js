export class SettingsModel {
    constructor() {
        this.settings = {
            port: 8080,
            contextSize: 32768,
            threads: 6,
            gpuLayers: -1
        };
    }

    getSettings() {
        return this.settings;
    }

    saveSettings(newSettings) {
        this.settings = { ...this.settings, ...newSettings };
        return this.settings;
    }
}
