import { SettingsModel } from '../models/settings_model.js';

const { ref } = Vue;

export function useSettingsController() {
    const model = new SettingsModel();
    const settings = ref(model.getSettings());

    const saveSettings = () => {
        model.saveSettings(settings.value);
        alert("Impostazioni salvate con successo in config.json!");
    };

    return {
        settings,
        saveSettings
    };
}
