<script setup lang="ts">
import { RouterLink } from 'vue-router'
import ArchitectureDiagram from '@/components/ArchitectureDiagram.vue'

const logoUrl = `${import.meta.env.BASE_URL}favicon.svg`
const principles = [
  {
    step: '01',
    title: '导入快照',
    text: '管理员选择本机目录，逐项预览允许与排除的文件，确认后生成不可变快照。',
  },
  {
    step: '02',
    title: '创建任务',
    text: '任务绑定一个快照和一种权限：只读审查，或写入隔离副本的受控修改。',
  },
  {
    step: '03',
    title: '受控执行',
    text: 'Agent 通过 MCP 调用文件工具；Broker 校验阶段、精确路径与写租约后才执行。',
  },
  {
    step: '04',
    title: '审计确认',
    text: 'intent 与 result 成对落盘；副本修改需管理员逐文件确认后才写回原项目。',
  },
]
const features = [
  {
    icon: '◷',
    title: '阶段权限',
    text: 'inspect → edit → verify → done 四阶段；只读任务固定为 inspect → verify → done，不可升级。',
  },
  {
    icon: '▤',
    title: '精确路径白名单',
    text: '只接受配置中逐条列出的相对路径；拒绝目录穿越、符号链接、Windows 重解析点与设备名。',
  },
  {
    icon: '◔',
    title: '限时限次租约',
    text: '写租约绑定任务与路径，有效期 1–300 秒、最多 1–10 次；单调时钟判定，成功写入即扣减。',
  },
  {
    icon: '⧉',
    title: '原子写入',
    text: '同目录临时文件 + 原子替换，保留原权限；失败不截断目标文件，也不消耗租约。',
  },
  {
    icon: '◎',
    title: '可关联审计',
    text: '每次调用记录 intent 与 result，以 request_id 串联；事件按服务实例隔离，重启不混淆。',
  },
  {
    icon: '⇄',
    title: '外部 Agent 接入',
    text: 'MCP stdio + 管道中继：外部 Harness 拿不到任务凭证，读写都经过围栏，修改需人工确认。',
  },
]
const boundaries = [
  '只保护经过 Broker / MCP 的调用，不是操作系统级沙箱。',
  '不抵御同权限进程的并发替换；工作区应是受信任的静态夹具。',
  '服务重启后任务、Token 与租约失效，JSONL 审计保留但不在线恢复。',
]
</script>
<template>
  <div class="landing">
    <header class="landing-nav">
      <RouterLink class="landing-brand" to="/">
        <img :src="logoUrl" alt="" width="32" height="32" />
        <span>AgentFence<small>LITE CONSOLE</small></span>
      </RouterLink>
      <nav class="landing-links" aria-label="页面导航">
        <a href="#principle">原理</a>
        <a href="#features">能力</a>
        <a href="#boundary">边界</a>
      </nav>
      <RouterLink class="button primary" to="/workspaces">进入控制台 ↗</RouterLink>
    </header>

    <section class="intro">
      <div class="intro-copy">
        <p class="eyebrow">TASK-AWARE TOOL BROKER</p>
        <span class="badge">Rust · MCP stdio · 本地回环</span>
        <h1>让 Agent 的文件操作，<br /><em>落在围栏之内。</em></h1>
        <p class="lede">
          AgentFence Lite 是一个用 Rust 实现的任务感知工具权限网关：阶段、路径、租约与审计四道约束，
          把 AI Agent 的文件读写关进可核验的边界。
        </p>
        <div class="intro-actions">
          <RouterLink class="button primary" to="/workspaces">进入控制台 ↗</RouterLink>
          <RouterLink class="button" to="/tasks">查看任务 →</RouterLink>
        </div>
        <p class="intro-meta">4 阶段状态机 · 3 个只读工具 · intent / result 双审计</p>
      </div>

      <ArchitectureDiagram />
    </section>

    <section class="stats">
      <article class="stat">
        <strong>4</strong>
        <p>阶段状态机</p>
        <span>inspect → edit → verify → done</span>
      </article>
      <article class="stat">
        <strong>3 + 1</strong>
        <p>MCP 工具</p>
        <span>只读三件套，写入仅限隔离副本</span>
      </article>
      <article class="stat">
        <strong>100%</strong>
        <p>调用留痕</p>
        <span>intent 与 result 成对记录</span>
      </article>
      <article class="stat">
        <strong>2</strong>
        <p>运行方式</p>
        <span>内置执行器 / 外部 DeepSeek Harness</span>
      </article>
    </section>

    <section id="principle" class="section">
      <p class="section-eyebrow">WORKFLOW</p>
      <h2>四步闭环，每一步都有证据。</h2>
      <p class="section-lede">从选择目录到写回原项目，权限只在必要的时间与路径上生效。</p>
      <div class="steps">
        <article v-for="item in principles" :key="item.step" class="step">
          <span class="step-no">{{ item.step }}</span>
          <h3>{{ item.title }}</h3>
          <p>{{ item.text }}</p>
        </article>
      </div>
    </section>

    <section id="features" class="section">
      <p class="section-eyebrow">CAPABILITIES</p>
      <h2>不是更聪明的 Agent，而是更可靠的围栏。</h2>
      <p class="section-lede">权限在服务端判定，不依赖前端隐藏按钮，也不依赖模型自觉。</p>
      <div class="cards">
        <article v-for="(item, index) in features" :key="item.title" class="card">
          <div class="card-head">
            <span class="card-icon">{{ item.icon }}</span>
            <span class="card-no">{{ String(index + 1).padStart(2, '0') }}</span>
          </div>
          <h3>{{ item.title }}</h3>
          <p>{{ item.text }}</p>
        </article>
      </div>
    </section>

    <section id="boundary" class="section">
      <p class="section-eyebrow">BOUNDARY</p>
      <h2>诚实标注安全边界。</h2>
      <div class="boundary">
        <ul>
          <li v-for="item in boundaries" :key="item">{{ item }}</li>
        </ul>
        <p>
          在经授权的工具调用路径内，AgentFence 提供最小权限执行保障；它不是整个 Agent 进程的操作系统级沙箱。
        </p>
      </div>
    </section>

    <footer class="landing-footer">
      <span>AgentFence Lite · MIT · Rust 1.98.1</span>
      <span>仅监听 127.0.0.1 · 凭证只在服务端</span>
    </footer>
  </div>
</template>
<style scoped>
.landing {
  --ink: #16241c;
  --muted: #5d7166;
  --line: #dde5d8;
  --tint: #f6f9f3;
  --brand: #2f5d44;
  min-height: 100vh;
  background: #fff;
  color: var(--ink);
  padding: 0 6vw 40px;
}
.landing-nav {
  display: flex;
  align-items: center;
  gap: 28px;
  padding: 22px 0;
  border-bottom: 1px solid var(--line);
}
.landing-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 19px;
  font-weight: 700;
  letter-spacing: -0.5px;
}
.landing-brand small {
  display: block;
  font-size: 9px;
  letter-spacing: 2.2px;
  color: #84938c;
  font-weight: 600;
  margin-top: 4px;
}
.landing-links {
  display: flex;
  gap: 26px;
  margin-left: auto;
  font-size: 14px;
  color: #50655a;
}
.landing-links a:hover {
  color: var(--brand);
}
.intro {
  background: #fff;
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(0, 0.95fr);
  gap: 56px;
  align-items: center;
  padding: 72px 0 56px;
}
.eyebrow {
  margin: 0 0 14px;
  font-size: 10px;
  letter-spacing: 2.6px;
  font-weight: 700;
  color: var(--brand);
}
.badge {
  display: inline-block;
  padding: 5px 10px;
  border: 1px solid #cfe0cd;
  border-radius: 30px;
  font-size: 11px;
  color: #4f6b56;
  background: #f2f8ee;
}
.intro h1 {
  font-size: clamp(32px, 3.6vw, 46px);
  line-height: 1.2;
  letter-spacing: -1.2px;
  margin: 22px 0 20px;
  font-weight: 750;
  text-wrap: balance;
}
.intro h1 em {
  font-style: normal;
  color: var(--brand);
}
.lede {
  max-width: 560px;
  font-size: 15px;
  line-height: 1.95;
  color: var(--muted);
}
.intro-actions {
  display: flex;
  gap: 14px;
  margin: 30px 0 18px;
}
.intro-meta {
  font-size: 12px;
  color: #84938c;
  letter-spacing: 0.3px;
}
.stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 18px;
  padding: 8px 0 20px;
}
.stat {
  border: 1px solid var(--line);
  border-radius: 14px;
  background: #fff;
  padding: 22px;
}
.stat strong {
  font-size: 34px;
  font-weight: 700;
  letter-spacing: -1px;
}
.stat p {
  margin: 4px 0 8px;
  font-size: 14px;
  font-weight: 600;
}
.stat span {
  font-size: 12px;
  color: var(--muted);
  line-height: 1.7;
}
.section {
  padding: 64px 0 24px;
  border-top: 1px solid var(--line);
  margin-top: 40px;
}
.section-eyebrow {
  margin: 0 0 10px;
  font-size: 10px;
  letter-spacing: 2.6px;
  font-weight: 700;
  color: var(--brand);
}
.section h2 {
  margin: 0 0 14px;
  font-size: clamp(24px, 2.6vw, 34px);
  letter-spacing: -0.8px;
}
.section-lede {
  margin: 0 0 34px;
  color: var(--muted);
  font-size: 14px;
  line-height: 1.9;
}
.steps {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 18px;
}
.step {
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 22px;
  background: var(--tint);
}
.step-no {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 1.6px;
  color: var(--brand);
}
.step h3 {
  margin: 14px 0 8px;
  font-size: 16px;
}
.step p {
  margin: 0;
  font-size: 13px;
  line-height: 1.85;
  color: var(--muted);
}
.cards {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 18px;
}
.card {
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 24px;
  background: #fff;
}
.card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.card-icon {
  width: 38px;
  height: 38px;
  border-radius: 10px;
  background: #eef5e9;
  color: var(--brand);
  display: grid;
  place-items: center;
  font-size: 18px;
}
.card-no {
  font-size: 11px;
  color: #9aab9d;
  letter-spacing: 1.4px;
}
.card h3 {
  margin: 18px 0 8px;
  font-size: 16px;
}
.card p {
  margin: 0;
  font-size: 13px;
  line-height: 1.85;
  color: var(--muted);
}
.boundary {
  border: 1px solid #e6ddc2;
  border-radius: 14px;
  background: #fbf8ec;
  padding: 26px 28px;
}
.boundary ul {
  margin: 0 0 14px;
  padding-left: 20px;
  line-height: 2;
  font-size: 13px;
  color: #6b6040;
}
.boundary p {
  margin: 0;
  font-size: 13px;
  line-height: 1.9;
  color: #7b6f4c;
}
.landing-footer {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  margin-top: 56px;
  padding-top: 22px;
  border-top: 1px solid var(--line);
  font-size: 11px;
  color: #8b9a8e;
}
@media (max-width: 1000px) {
  .intro { grid-template-columns: 1fr; gap: 36px; }
  .stats, .steps, .cards { grid-template-columns: repeat(2, 1fr); }
}
@media (max-width: 640px) {
  .landing { padding: 0 20px 30px; }
  .landing-links { display: none; }
  .stats, .steps, .cards { grid-template-columns: 1fr; }
  .landing-footer { flex-direction: column; }
}
</style>