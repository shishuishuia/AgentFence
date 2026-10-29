<script setup lang="ts">
// Compact inline architecture diagram; no external assets, safe under a strict CSP.
</script>
<template>
  <figure class="architecture">
    <svg viewBox="0 0 540 470" role="img" aria-labelledby="arch-title arch-desc">
      <title id="arch-title">AgentFence Lite 架构</title>
      <desc id="arch-desc">
        管理员浏览器经本地代理、外部 Agent 经 MCP 适配器访问 Rust Broker；Broker 校验阶段、路径与租约后
        读写快照或隔离副本，管理员确认后才写回原项目。
      </desc>
      <defs>
        <marker
          id="arch-arrow"
          viewBox="0 0 10 10"
          refX="9"
          refY="5"
          markerWidth="6"
          markerHeight="6"
          orient="auto-start-reverse"
        >
          <path d="M0,0 L10,5 L0,10 z" fill="#7f9a88" />
        </marker>
      </defs>

      <rect
        x="8"
        y="8"
        width="524"
        height="456"
        rx="18"
        fill="#f8fbf6"
        stroke="#d3e1d0"
        stroke-dasharray="6 6"
      />
      <text class="zone" x="24" y="32">本机回环 · 可信边界</text>

      <g class="box">
        <rect x="24" y="44" width="220" height="58" rx="9" />
        <text class="title" x="40" y="70">管理员 / 浏览器</text>
        <text class="sub" x="40" y="88">Vue 控制台 · 仅同源查询</text>
      </g>
      <g class="box">
        <rect x="296" y="44" width="220" height="58" rx="9" />
        <text class="title" x="312" y="70">外部 Agent / Harness</text>
        <text class="sub" x="312" y="88">仅经 MCP，拿不到凭证</text>
      </g>

      <line class="arrow" x1="134" y1="102" x2="134" y2="134" marker-end="url(#arch-arrow)" />
      <line class="arrow" x1="406" y1="102" x2="406" y2="134" marker-end="url(#arch-arrow)" />

      <g class="box">
        <rect x="24" y="136" width="220" height="58" rx="9" />
        <text class="title" x="40" y="162">web_proxy · 本地代理</text>
        <text class="sub" x="40" y="180">持有 Admin Token</text>
      </g>
      <g class="box">
        <rect x="296" y="136" width="220" height="58" rx="9" />
        <text class="title" x="312" y="162">agentfence-mcp-server</text>
        <text class="sub" x="312" y="180">持有 Task Token · 工具白名单</text>
      </g>

      <line class="arrow" x1="134" y1="194" x2="134" y2="234" marker-end="url(#arch-arrow)" />
      <line class="arrow" x1="406" y1="194" x2="406" y2="234" marker-end="url(#arch-arrow)" />
      <text class="wire" x="144" y="220">/api</text>
      <text class="wire" x="416" y="220">HTTP 127.0.0.1</text>

      <rect x="24" y="236" width="492" height="92" rx="11" fill="#ffffff" stroke="#7fae8d" />
      <rect x="24" y="236" width="5" height="92" rx="2.5" fill="#2f5d44" />
      <text class="title strong" x="46" y="266">agentfence-core + agentfence-server · Broker</text>
      <text class="sub" x="46" y="286">阶段判定 · 精确路径 · 限时租约 · intent/result 双审计</text>
      <text class="sub" x="46" y="304">同目录临时文件 + 原子替换；失败不截断、不扣次数</text>

      <line class="arrow" x1="150" y1="328" x2="150" y2="364" marker-end="url(#arch-arrow)" />
      <text class="wire" x="160" y="350">校验通过才执行</text>

      <g class="box">
        <rect x="24" y="366" width="240" height="56" rx="9" />
        <text class="title" x="40" y="392">工作区快照 / 隔离副本</text>
        <text class="sub" x="40" y="409">不可变快照 · 可编辑副本</text>
      </g>
      <g class="box dashed">
        <rect x="320" y="366" width="196" height="56" rx="9" />
        <text class="title" x="336" y="392">原项目目录</text>
        <text class="sub" x="336" y="409">管理员确认后写回</text>
      </g>
      <line class="arrow" x1="264" y1="394" x2="318" y2="394" marker-end="url(#arch-arrow)" />
      <text class="wire" x="272" y="384">确认</text>

      <text class="footnote" x="24" y="446">Admin Token 只在代理进程；Task Token 只在 MCP 子进程；两者都不进入浏览器或模型上下文。</text>
    </svg>
  </figure>
</template>
<style scoped>
.architecture {
  margin: 0;
  border: 1px solid #dde5d8;
  border-radius: 18px;
  background: #f8fbf6;
  padding: 16px;
  box-shadow: 0 10px 30px rgba(23, 37, 29, 0.06);
}
svg {
  display: block;
  width: 100%;
  height: auto;
  font-family: inherit;
}
.box rect {
  fill: #ffffff;
  stroke: #d7e0d1;
}
.box.dashed rect {
  fill: #fbfcf9;
  stroke-dasharray: 4 4;
}
.title {
  fill: #1f3329;
  font-size: 12.5px;
  font-weight: 600;
}
.title.strong {
  font-weight: 700;
}
.sub {
  fill: #6d8175;
  font-size: 10px;
}
.zone {
  fill: #7b8f80;
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 1.4px;
}
.wire {
  fill: #8aa090;
  font-size: 8.5px;
}
.footnote {
  fill: #8aa090;
  font-size: 8.5px;
}
.arrow {
  stroke: #7f9a88;
  stroke-width: 1.3;
}
</style>
