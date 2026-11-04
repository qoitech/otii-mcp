# Otii MCP server

An [MCP](https://modelcontextprotocol.io) server that lets AI assistants measure the energy consumption of your device with a Qoitech **Otii Arc** or **Otii Ace**. You can ask your assistant things like:

> Power my board at 3.3 V for 20 seconds and tell me how much energy one cycle uses.

> I changed the firmware to sleep between transmissions, measure again and compare with the previous recording.

The server powers your device under test from the Otii, records current and (optionally) the device's UART log, and computes energy, charge and current statistics. These can cover a whole recording, a time range, or the interval between two log messages.

> **Early preview.** Tool names and parameters may change. Feedback is welcome through GitHub issues.

## Requirements

- An Otii Arc or Otii Ace connected to your computer.
- The **Otii 3** desktop application running, with its **TCP server enabled**.
- An Otii account with an **Automation** license available. The server reserves one license for each tool call and returns it afterwards.
- [uv](https://docs.astral.sh/uv/getting-started/installation/), used to run the server without a manual install.

## Configuration

The server is configured through environment variables, set in your MCP client's configuration:

| Variable | Default | Description |
|---|---|---|
| `OTII_CREDENTIALS` | | Path to a JSON file with your Otii login (see `credentials.example.json`). |
| `OTII_USERNAME`, `OTII_PASSWORD` | | Otii login, used when `OTII_CREDENTIALS` is not set or the file is missing. |
| `OTII_HOST` | `127.0.0.1` | Host running the Otii application. |
| `OTII_PORT` | `1905` | Otii TCP server port. |
| `OTII_DEVICE` | | Name of the Otii device to use. Only needed when more than one is connected. |

If the Otii application is already logged in, no credentials are needed.

A credentials file keeps your password out of the MCP client's configuration:

```json
{
  "username": "your-otii-account@example.com",
  "password": "your-otii-password"
}
```

## Adding the server to your MCP client

### Claude Code

```sh
claude mcp add otii --env OTII_CREDENTIALS=$HOME/.config/otii/credentials.json \
  -- uvx --from git+https://github.com/qoitech/otii-mcp otii-mcp
```

Add `--scope project` to write it to the repository's `.mcp.json`, so that everyone working on the firmware gets it.

### Claude Desktop, Cursor, VS Code and other clients

Most clients use the same JSON format: `claude_desktop_config.json` for Claude Desktop, `.cursor/mcp.json` for Cursor, and `.vscode/mcp.json` (under `"servers"`) for VS Code.

```json
{
  "mcpServers": {
    "otii": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/qoitech/otii-mcp", "otii-mcp"],
      "env": {
        "OTII_CREDENTIALS": "/path/to/credentials.json"
      }
    }
  }
}
```

### From a local clone

```sh
git clone https://github.com/qoitech/otii-mcp
```

Then use `"command": "uv"` with `"args": ["run", "--directory", "/path/to/otii-mcp", "otii-mcp"]`.

## Tools

| Tool | Description |
|---|---|
| `list_devices` | List connected Otii devices. |
| `measure` | Power the device under test at a given voltage for a set time and record it. Returns a recording id and whole-recording statistics. Optionally records UART output and extra channels. |
| `list_recordings` | List recordings in the active Otii project. |
| `get_log` | Read the recorded UART log, with timestamps, optionally filtered. |
| `get_statistics` | Energy, charge and min/max/average current between two timestamps. |
| `energy_between_messages` | Energy between two UART log lines, e.g. one firmware cycle. |
| `set_gpo` | Switch Otii digital output GPO 1 or 2. |
| `set_ace_0_15_voltage` | Set the voltage of the Ace 0–15 V pin. |

Recordings are stored in the project that is open in the Otii application, so you can open them there for a closer look.

## Measuring per-cycle energy with UART log markers

The most useful measurements come from combining the current with your firmware's log output:

1. Connect the device's UART TX to the Otii RX, and set `exp_voltage` to the device's IO voltage.
2. Make your firmware print a fixed line once per cycle, e.g. `Connecting...`.
3. Ask the assistant to measure with `uart_baudrate` set. It then calls `energy_between_messages` with that line to get the energy of one cycle. `occurrence: 1` skips the first cycle, which includes boot.

**Tip:** if your device is also connected over USB for flashing or debugging, it may be partly powered from USB, which makes the measurements wrong. Use `set_gpo` to switch a USB power switch off while measuring.

## Safety

`measure` turns on the Otii main output at the voltage you, or the assistant, choose. Tell the assistant your device's supply voltage, and set `max_current_A` to a sensible limit. Consider allowing `measure`, `set_gpo` and `set_ace_0_15_voltage` only after confirmation in your MCP client.

## License

MIT
