# Neovim MCP

## Experimental Neovim Integration

This project is a **series of experiments** exploring integration between Neovim and MCP (Model Context Protocol). It provides a minimal, synchronous connection to Neovim that avoids event loops.

## Purpose

The primary goal of this experimental project is to:

1. Demonstrate how Neovim can be controlled via a socket connection
2. Provide basic file and buffer manipulation capabilities
3. Test the viability of integrating MCP with Neovim

## Getting Started

### Prerequisites

- Python 3.11 or higher
- Neovim with socket support
- MCP Python package

### Usage

Start Neovim with socket support:

```bash
nvim --listen /path/to/project/neovim.socket
```

When working with Claude Desktop, you must specify your project path by providing:

```
Use this PROJECT_PATH=/path/to/project
```

Notice the PROJECT_PATH must be the same the socket was created at. This ensures Claude can properly connect to the Neovim socket in your environment. Without this path, Claude will not be able to access your Neovim instance.

## Features

- Basic buffer operations (read/write)
- File system operations
- Project structure exploration
- Context-aware text updates

## Status

⚠️ **EXPERIMENTAL** ⚠️

This is an experimental project and not fully intended for production use. APIs may change without notice, and functionality is not guaranteed.

## License

[MIT](LICENSE)
