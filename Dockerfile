# TCP Security System with Ollama on Ubuntu
FROM ubuntu:22.04

# Avoid interactive prompts during installation
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /tcp-security

# Install system dependencies
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    curl \
    wget \
    zstd \
    man-db \
    manpages \
    manpages-dev \
    build-essential \
    git \
    vim \
    less \
    grep \
    coreutils \
    util-linux \
    net-tools \
    iputils-ping \
    && rm -rf /var/lib/apt/lists/*

# Install Ollama
RUN curl -fsSL https://ollama.ai/install.sh | sh

# Create a non-root user for running the application
RUN useradd -m -s /bin/bash tcpuser && \
    usermod -aG sudo tcpuser

# Copy TCP security system files
COPY tcp/ ./tcp/
COPY *.py ./
COPY *.md ./

# Set up Python virtual environment
RUN python3 -m venv /opt/tcp-venv && \
    /opt/tcp-venv/bin/pip install --upgrade pip

# Create startup script with echo commands
RUN echo '#!/bin/bash' > /tcp-security/start-tcp-system.sh && \
    echo '' >> /tcp-security/start-tcp-system.sh && \
    echo 'echo "🐳 Starting TCP Security System in Docker"' >> /tcp-security/start-tcp-system.sh && \
    echo 'echo "=========================================="' >> /tcp-security/start-tcp-system.sh && \
    echo '' >> /tcp-security/start-tcp-system.sh && \
    echo '# Start Ollama service in background' >> /tcp-security/start-tcp-system.sh && \
    echo 'echo "🚀 Starting Ollama service..."' >> /tcp-security/start-tcp-system.sh && \
    echo 'ollama serve > /tmp/ollama.log 2>&1 &' >> /tcp-security/start-tcp-system.sh && \
    echo 'OLLAMA_PID=$!' >> /tcp-security/start-tcp-system.sh && \
    echo '' >> /tcp-security/start-tcp-system.sh && \
    echo '# Wait for Ollama to be ready' >> /tcp-security/start-tcp-system.sh && \
    echo 'echo "⏳ Waiting for Ollama to be ready..."' >> /tcp-security/start-tcp-system.sh && \
    echo 'for i in {1..30}; do' >> /tcp-security/start-tcp-system.sh && \
    echo '    if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then' >> /tcp-security/start-tcp-system.sh && \
    echo '        echo "✅ Ollama is ready!"' >> /tcp-security/start-tcp-system.sh && \
    echo '        break' >> /tcp-security/start-tcp-system.sh && \
    echo '    fi' >> /tcp-security/start-tcp-system.sh && \
    echo '    if [ $i -eq 30 ]; then' >> /tcp-security/start-tcp-system.sh && \
    echo '        echo "❌ Ollama failed to start in 30 seconds"' >> /tcp-security/start-tcp-system.sh && \
    echo '        exit 1' >> /tcp-security/start-tcp-system.sh && \
    echo '    fi' >> /tcp-security/start-tcp-system.sh && \
    echo '    sleep 1' >> /tcp-security/start-tcp-system.sh && \
    echo 'done' >> /tcp-security/start-tcp-system.sh && \
    echo '' >> /tcp-security/start-tcp-system.sh && \
    echo '# Pull the default model' >> /tcp-security/start-tcp-system.sh && \
    echo 'echo "📥 Pulling llama3.2:latest model (this may take a while)..."' >> /tcp-security/start-tcp-system.sh && \
    echo 'ollama pull llama3.2:latest' >> /tcp-security/start-tcp-system.sh && \
    echo '' >> /tcp-security/start-tcp-system.sh && \
    echo 'echo "🎯 Ollama setup complete!"' >> /tcp-security/start-tcp-system.sh && \
    echo 'echo ""' >> /tcp-security/start-tcp-system.sh && \
    echo 'echo "🔐 TCP Security System is ready!"' >> /tcp-security/start-tcp-system.sh && \
    echo 'echo "================================"' >> /tcp-security/start-tcp-system.sh && \
    echo 'echo ""' >> /tcp-security/start-tcp-system.sh && \
    echo 'echo "Available commands:"' >> /tcp-security/start-tcp-system.sh && \
    echo 'echo "  python3 tcp/local_ollama_demo.py        - Run local Ollama demonstration"' >> /tcp-security/start-tcp-system.sh && \
    echo 'echo "  python3 tcp/demo_complete_security_system.py - Run complete security demo"' >> /tcp-security/start-tcp-system.sh && \
    echo 'echo "  python3 tcp/enrichment/manpage_enricher.py  - Test man page enrichment"' >> /tcp-security/start-tcp-system.sh && \
    echo 'echo "  python3 tcp/security/secure_tcp_agent.py    - Test secure agent"' >> /tcp-security/start-tcp-system.sh && \
    echo 'echo ""' >> /tcp-security/start-tcp-system.sh && \
    echo 'echo "📊 System Status:"' >> /tcp-security/start-tcp-system.sh && \
    echo 'echo "  Ollama:    ✅ Running (PID: $OLLAMA_PID)"' >> /tcp-security/start-tcp-system.sh && \
    echo 'echo "  Model:     ✅ llama3.2:latest"' >> /tcp-security/start-tcp-system.sh && \
    echo 'echo "  TCP:       ✅ Ready"' >> /tcp-security/start-tcp-system.sh && \
    echo 'echo "  Ubuntu:    ✅ $(lsb_release -d | cut -f2)"' >> /tcp-security/start-tcp-system.sh && \
    echo 'echo ""' >> /tcp-security/start-tcp-system.sh && \
    echo 'echo "🏠 All processing is local - no external APIs needed!"' >> /tcp-security/start-tcp-system.sh && \
    echo 'echo ""' >> /tcp-security/start-tcp-system.sh && \
    echo '' >> /tcp-security/start-tcp-system.sh && \
    echo '# Keep container running and show logs' >> /tcp-security/start-tcp-system.sh && \
    echo 'echo "📋 Monitoring Ollama service..."' >> /tcp-security/start-tcp-system.sh && \
    echo 'echo "   (Ctrl+C to stop, then '\''exit'\'' to close container)"' >> /tcp-security/start-tcp-system.sh && \
    echo 'echo ""' >> /tcp-security/start-tcp-system.sh && \
    echo '' >> /tcp-security/start-tcp-system.sh && \
    echo '# Function to cleanup on exit' >> /tcp-security/start-tcp-system.sh && \
    echo 'cleanup() {' >> /tcp-security/start-tcp-system.sh && \
    echo '    echo ""' >> /tcp-security/start-tcp-system.sh && \
    echo '    echo "🛑 Shutting down TCP Security System..."' >> /tcp-security/start-tcp-system.sh && \
    echo '    kill $OLLAMA_PID 2>/dev/null' >> /tcp-security/start-tcp-system.sh && \
    echo '    echo "✅ Ollama stopped"' >> /tcp-security/start-tcp-system.sh && \
    echo '    exit 0' >> /tcp-security/start-tcp-system.sh && \
    echo '}' >> /tcp-security/start-tcp-system.sh && \
    echo '' >> /tcp-security/start-tcp-system.sh && \
    echo '# Set trap for cleanup' >> /tcp-security/start-tcp-system.sh && \
    echo 'trap cleanup SIGINT SIGTERM' >> /tcp-security/start-tcp-system.sh && \
    echo '' >> /tcp-security/start-tcp-system.sh && \
    echo '# Monitor Ollama and keep container alive' >> /tcp-security/start-tcp-system.sh && \
    echo 'while kill -0 $OLLAMA_PID 2>/dev/null; do' >> /tcp-security/start-tcp-system.sh && \
    echo '    sleep 5' >> /tcp-security/start-tcp-system.sh && \
    echo 'done' >> /tcp-security/start-tcp-system.sh && \
    echo '' >> /tcp-security/start-tcp-system.sh && \
    echo 'echo "❌ Ollama service stopped unexpectedly"' >> /tcp-security/start-tcp-system.sh && \
    echo 'exit 1' >> /tcp-security/start-tcp-system.sh

# Make startup script executable
RUN chmod +x /tcp-security/start-tcp-system.sh

# Create demo runner script with echo commands
RUN echo '#!/bin/bash' > /tcp-security/run-demo.sh && \
    echo '' >> /tcp-security/run-demo.sh && \
    echo 'echo "🎬 TCP Security System Demo Runner"' >> /tcp-security/run-demo.sh && \
    echo 'echo "=================================="' >> /tcp-security/run-demo.sh && \
    echo 'echo ""' >> /tcp-security/run-demo.sh && \
    echo 'echo "Which demonstration would you like to run?"' >> /tcp-security/run-demo.sh && \
    echo 'echo ""' >> /tcp-security/run-demo.sh && \
    echo 'echo "1) Local Ollama Demo (Privacy-first with local LLM)"' >> /tcp-security/run-demo.sh && \
    echo 'echo "2) Complete Security System Demo (Full integration)"' >> /tcp-security/run-demo.sh && \
    echo 'echo "3) Man Page Enrichment Test"' >> /tcp-security/run-demo.sh && \
    echo 'echo "4) Secure Agent Test"' >> /tcp-security/run-demo.sh && \
    echo 'echo "5) Interactive Shell"' >> /tcp-security/run-demo.sh && \
    echo 'echo ""' >> /tcp-security/run-demo.sh && \
    echo 'read -p "Choose option (1-5): " choice' >> /tcp-security/run-demo.sh && \
    echo '' >> /tcp-security/run-demo.sh && \
    echo 'case $choice in' >> /tcp-security/run-demo.sh && \
    echo '    1)' >> /tcp-security/run-demo.sh && \
    echo '        echo "🏠 Running Local Ollama Demo..."' >> /tcp-security/run-demo.sh && \
    echo '        python3 tcp/local_ollama_demo.py' >> /tcp-security/run-demo.sh && \
    echo '        ;;' >> /tcp-security/run-demo.sh && \
    echo '    2)' >> /tcp-security/run-demo.sh && \
    echo '        echo "🔐 Running Complete Security System Demo..."' >> /tcp-security/run-demo.sh && \
    echo '        python3 tcp/demo_complete_security_system.py' >> /tcp-security/run-demo.sh && \
    echo '        ;;' >> /tcp-security/run-demo.sh && \
    echo '    3)' >> /tcp-security/run-demo.sh && \
    echo '        echo "📚 Testing Man Page Enrichment..."' >> /tcp-security/run-demo.sh && \
    echo '        python3 tcp/enrichment/manpage_enricher.py' >> /tcp-security/run-demo.sh && \
    echo '        ;;' >> /tcp-security/run-demo.sh && \
    echo '    4)' >> /tcp-security/run-demo.sh && \
    echo '        echo "🤖 Testing Secure Agent..."' >> /tcp-security/run-demo.sh && \
    echo '        python3 tcp/security/secure_tcp_agent.py' >> /tcp-security/run-demo.sh && \
    echo '        ;;' >> /tcp-security/run-demo.sh && \
    echo '    5)' >> /tcp-security/run-demo.sh && \
    echo '        echo "🐚 Starting interactive shell..."' >> /tcp-security/run-demo.sh && \
    echo '        echo "Available commands:"' >> /tcp-security/run-demo.sh && \
    echo '        echo "  python3 tcp/local_ollama_demo.py"' >> /tcp-security/run-demo.sh && \
    echo '        echo "  python3 tcp/demo_complete_security_system.py"' >> /tcp-security/run-demo.sh && \
    echo '        echo "  ollama list"' >> /tcp-security/run-demo.sh && \
    echo '        echo "  ollama ps"' >> /tcp-security/run-demo.sh && \
    echo '        bash' >> /tcp-security/run-demo.sh && \
    echo '        ;;' >> /tcp-security/run-demo.sh && \
    echo '    *)' >> /tcp-security/run-demo.sh && \
    echo '        echo "❌ Invalid option. Please choose 1-5."' >> /tcp-security/run-demo.sh && \
    echo '        exit 1' >> /tcp-security/run-demo.sh && \
    echo '        ;;' >> /tcp-security/run-demo.sh && \
    echo 'esac' >> /tcp-security/run-demo.sh

RUN chmod +x /tcp-security/run-demo.sh

# Create health check script with echo commands
RUN echo '#!/bin/bash' > /tcp-security/health-check.sh && \
    echo '' >> /tcp-security/health-check.sh && \
    echo 'echo "🏥 TCP Security System Health Check"' >> /tcp-security/health-check.sh && \
    echo 'echo "==================================="' >> /tcp-security/health-check.sh && \
    echo '' >> /tcp-security/health-check.sh && \
    echo '# Check Ollama' >> /tcp-security/health-check.sh && \
    echo 'if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then' >> /tcp-security/health-check.sh && \
    echo '    echo "✅ Ollama service: Running"' >> /tcp-security/health-check.sh && \
    echo '    ' >> /tcp-security/health-check.sh && \
    echo '    # Check models' >> /tcp-security/health-check.sh && \
    echo '    models=$(ollama list 2>/dev/null | grep -v "NAME" | wc -l)' >> /tcp-security/health-check.sh && \
    echo '    if [ $models -gt 0 ]; then' >> /tcp-security/health-check.sh && \
    echo '        echo "✅ Ollama models: $models available"' >> /tcp-security/health-check.sh && \
    echo '        ollama list' >> /tcp-security/health-check.sh && \
    echo '    else' >> /tcp-security/health-check.sh && \
    echo '        echo "⚠️  Ollama models: None installed"' >> /tcp-security/health-check.sh && \
    echo '    fi' >> /tcp-security/health-check.sh && \
    echo 'else' >> /tcp-security/health-check.sh && \
    echo '    echo "❌ Ollama service: Not running"' >> /tcp-security/health-check.sh && \
    echo 'fi' >> /tcp-security/health-check.sh && \
    echo '' >> /tcp-security/health-check.sh && \
    echo '# Check Python' >> /tcp-security/health-check.sh && \
    echo 'if python3 --version > /dev/null 2>&1; then' >> /tcp-security/health-check.sh && \
    echo '    echo "✅ Python: $(python3 --version)"' >> /tcp-security/health-check.sh && \
    echo 'else' >> /tcp-security/health-check.sh && \
    echo '    echo "❌ Python: Not available"' >> /tcp-security/health-check.sh && \
    echo 'fi' >> /tcp-security/health-check.sh && \
    echo '' >> /tcp-security/health-check.sh && \
    echo '# Check TCP system files' >> /tcp-security/health-check.sh && \
    echo 'if [ -f "tcp/local_ollama_demo.py" ]; then' >> /tcp-security/health-check.sh && \
    echo '    echo "✅ TCP system: Files present"' >> /tcp-security/health-check.sh && \
    echo 'else' >> /tcp-security/health-check.sh && \
    echo '    echo "❌ TCP system: Files missing"' >> /tcp-security/health-check.sh && \
    echo 'fi' >> /tcp-security/health-check.sh && \
    echo '' >> /tcp-security/health-check.sh && \
    echo '# Check man pages' >> /tcp-security/health-check.sh && \
    echo 'if man ls > /dev/null 2>&1; then' >> /tcp-security/health-check.sh && \
    echo '    echo "✅ Man pages: Available"' >> /tcp-security/health-check.sh && \
    echo 'else' >> /tcp-security/health-check.sh && \
    echo '    echo "❌ Man pages: Not available"' >> /tcp-security/health-check.sh && \
    echo 'fi' >> /tcp-security/health-check.sh && \
    echo '' >> /tcp-security/health-check.sh && \
    echo '# System info' >> /tcp-security/health-check.sh && \
    echo 'echo ""' >> /tcp-security/health-check.sh && \
    echo 'echo "📊 System Information:"' >> /tcp-security/health-check.sh && \
    echo 'echo "   OS: $(lsb_release -d | cut -f2)"' >> /tcp-security/health-check.sh && \
    echo 'echo "   Kernel: $(uname -r)"' >> /tcp-security/health-check.sh && \
    echo 'echo "   Architecture: $(uname -m)"' >> /tcp-security/health-check.sh && \
    echo 'echo "   Memory: $(free -h | grep Mem | awk '\''\{print $2}'\''\)"' >> /tcp-security/health-check.sh && \
    echo 'echo "   Disk: $(df -h / | tail -1 | awk '\''\{print $4}'\''\) available"' >> /tcp-security/health-check.sh

RUN chmod +x /tcp-security/health-check.sh

# Copy interactive TCP shell script
COPY scripts/tcp-shell.sh /tcp-security/tcp-shell.sh
RUN chmod +x /tcp-security/tcp-shell.sh

# Set proper ownership
RUN chown -R tcpuser:tcpuser /tcp-security

# Switch to non-root user
USER tcpuser

# Set environment variables
ENV PATH="/opt/tcp-venv/bin:$PATH"
ENV OLLAMA_HOST="0.0.0.0:11434"

# Expose Ollama port (optional, for external access)
EXPOSE 11434

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:11434/api/tags || exit 1

# Default command starts the full system
CMD ["/tcp-security/start-tcp-system.sh"]
