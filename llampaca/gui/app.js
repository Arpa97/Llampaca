import ChatView from './components/ChatView.js';
import ModelsView from './components/ModelsView.js';
import McpView from './components/McpView.js';
import SettingsView from './components/SettingsView.js';

const { createApp, ref } = Vue;

createApp({
    components: {
        ChatView,
        ModelsView,
        McpView,
        SettingsView
    },
    setup() {
        const currentTab = ref('chat');

        return {
            currentTab
        };
    }
}).mount('#app');
