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
        const toastMessage = ref('');
        const toastType = ref('success');

        window.showToast = (msg, type = 'success') => {
            toastMessage.value = msg;
            toastType.value = type;
            setTimeout(() => {
                toastMessage.value = '';
            }, 3500);
        };

        return {
            currentTab,
            toastMessage,
            toastType
        };
    }
}).mount('#app');
