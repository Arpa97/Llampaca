export class McpModel {
    constructor() {
        this.activeIntegrations = [
            {
                name: 'email-mcp-server',
                command: 'node',
                args: ['/Users/andreadarpa/.llampaca/integrations/email-mcp-server/build/index.js'],
                toolsCount: 28
            }
        ];

        this.registryItems = [
            {
                name: 'gmail-mcp',
                description: 'Server locale MCP per connettere molteplici account Gmail e Google Calendar.',
                repo: 'https://github.com/mareyam/ClaudeCode-Gmail-GDrive-GCalendar-Custom-MCP-Server-Mac-Setup'
            },
            {
                name: 'email-mcp-server',
                description: 'Integrazione universale per client mail, supporta server IMAP e SMTP esterni.',
                repo: 'https://github.com/Aftab-web-dev/email-mcp-server'
            },
            {
                name: 'sqlite-mcp-server',
                description: 'Consente all\'agente di leggere, scrivere e manipolare database SQLite locali.',
                repo: 'https://github.com/modelcontextprotocol/servers/tree/main/src/sqlite'
            },
            {
                name: 'postgres-mcp',
                description: 'Consente di connettersi ad istanze Postgres locali o in rete per query SQL in linguaggio naturale.',
                repo: 'https://github.com/modelcontextprotocol/servers/tree/main/src/postgres'
            },
            {
                name: 'deadends.dev',
                description: 'Aiuta gli agenti di programmazione a evitare di ripetere errori noti o loop infiniti.',
                repo: 'https://github.com/dbwls99706/deadends.dev'
            }
        ];
    }

    getActiveIntegrations() {
        return this.activeIntegrations;
    }

    getRegistryItems() {
        return this.registryItems;
    }

    removeIntegration(name) {
        this.activeIntegrations = this.activeIntegrations.filter(i => i.name !== name);
    }

    installIntegration(item) {
        this.activeIntegrations.push({
            name: item.name,
            command: 'npx',
            args: ['-y', item.name],
            toolsCount: 5
        });
    }
}
