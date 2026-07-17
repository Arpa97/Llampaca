export class GgufModel {
    constructor() {
        this.models = [
            {
                id: 1,
                name: 'Qwen3.5-4B-Q8_0.gguf',
                description: 'Modello predefinito di eccellenti prestazioni generali e di ragionamento.',
                size: '4.3 GB',
                quant: 'Q8_0',
                active: true
            },
            {
                id: 2,
                name: 'qwen2.5-coder-1.5b-instruct-q4_k_m.gguf',
                description: 'Modello super compatto e velocissimo specifico per la generazione di codice.',
                size: '1.2 GB',
                quant: 'Q4_K_M',
                active: false
            }
        ];
    }

    getModels() {
        return this.models;
    }

    setDefaultModel(id) {
        this.models.forEach(m => m.active = (m.id === id));
    }

    deleteModel(id) {
        this.models = this.models.filter(m => m.id !== id);
    }

    addModel(name, description, size, quant) {
        const nextId = this.models.length ? Math.max(...this.models.map(m => m.id)) + 1 : 1;
        const newModel = { id: nextId, name, description, size, quant, active: false };
        this.models.push(newModel);
        return newModel;
    }
}
