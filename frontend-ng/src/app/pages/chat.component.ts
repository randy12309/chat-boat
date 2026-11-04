import { HttpErrorResponse } from '@angular/common/http';
import {
  AfterRenderRef,
  Component,
  DestroyRef,
  Directive,
  ElementRef,
  Injector,
  afterNextRender,
  computed,
  inject,
  signal,
} from '@angular/core';
import { CurrencyPipe, DatePipe, NgTemplateOutlet } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';
import { AuthService } from '../core/auth.service';
import { ChatService, ChatSession, ChatUi, DueItem, HistoryMessage, HomeData } from '../core/chat.service';
import { MarkdownPipe } from '../core/markdown.pipe';

interface PayCard {
  items: DueItem[];
  selected: string[];
  authorized: boolean;
  paying: boolean;
  error: string | null;
}

interface Message {
  role: 'user' | 'assistant';
  text: string;
  time: Date | null;
  ui?: ChatUi | null;
  answered?: boolean;
  card?: PayCard;
}

interface SessionGroup {
  label: string;
  items: ChatSession[];
}

@Directive({ selector: '[appFocusSelect]' })
export class FocusSelectDirective {
  constructor() {
    const el = inject<ElementRef<HTMLInputElement>>(ElementRef).nativeElement;
    afterNextRender(() => {
      el.focus();
      el.select();
    });
  }
}

function newSessionId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return 's' + Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

function groupSessions(list: ChatSession[]): SessionGroup[] {
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const day = 86_400_000;
  const buckets = [
    { label: 'Today', items: [] as ChatSession[], min: startOfToday.getTime() },
    { label: 'Yesterday', items: [] as ChatSession[], min: startOfToday.getTime() - day },
    { label: 'Previous 7 days', items: [] as ChatSession[], min: startOfToday.getTime() - 7 * day },
    { label: 'Older', items: [] as ChatSession[], min: -Infinity },
  ];
  for (const s of list) {
    const t = s.updated_at * 1000;
    buckets.find((b) => t >= b.min)?.items.push(s);
  }
  return buckets.filter((b) => b.items.length);
}

@Component({
  selector: 'app-chat',
  imports: [FormsModule, CurrencyPipe, DatePipe, NgTemplateOutlet, MarkdownPipe, FocusSelectDirective],
  templateUrl: './chat.component.html',
})
export class ChatComponent {
  private readonly api = inject(ChatService);
  protected readonly auth = inject(AuthService);
  private readonly injector = inject(Injector);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);

  readonly messages = signal<Message[]>([]);
  readonly sessions = signal<ChatSession[]>([]);
  readonly groups = computed(() => groupSessions(this.sessions()));
  readonly sessionId = signal(newSessionId());
  readonly input = signal('');
  readonly sending = signal(false);
  readonly loadingHistory = signal(false);
  readonly error = signal<string | null>(null);
  readonly sidebarError = signal<string | null>(null);
  readonly sidebarOpen = signal(false);
  readonly sessionsLoaded = signal(false);
  readonly editingId = signal<string | null>(null);
  editTitle = '';
  readonly home = signal<HomeData | null>(null);
  readonly homeError = signal<string | null>(null);

  readonly isSaved = computed(() => this.sessions().some((s) => s.session_id === this.sessionId()));
  readonly showWelcome = computed(() => !this.messages().length && !this.loadingHistory());
  readonly actionNeeded = computed(() => {
    const p = this.home()?.premium_alert;
    return !!p && p.status !== 'Paid';
  });

  private lastFailed: string | null = null;
  private retryOpenId: string | null = null;

  constructor() {
    void this.loadHome();
    const timer = setInterval(() => void this.loadHome(true), 30000);
    const onFocus = () => void this.loadHome(true);
    window.addEventListener('focus', onFocus);
    inject(DestroyRef).onDestroy(() => {
      clearInterval(timer);
      window.removeEventListener('focus', onFocus);
    });
    void this.refreshSessions().then(() => {
      const first = this.sessions()[0];
      if (first) {
        this.sessionId.set('');
        void this.open(first.session_id);
      }
    });
  }

  async loadHome(silent = false): Promise<void> {
    if (!silent) this.homeError.set(null);
    try {
      this.home.set(await firstValueFrom(this.api.home()));
      this.homeError.set(null);
    } catch {
      if (!silent) this.homeError.set('Could not load your account summary.');
    }
  }

  async refreshSessions(): Promise<void> {
    try {
      this.sessions.set(await firstValueFrom(this.api.sessions()));
      this.sidebarError.set(null);
    } catch {
      this.sidebarError.set('Could not load your chats.');
    } finally {
      this.sessionsLoaded.set(true);
    }
  }

  async open(id: string): Promise<void> {
    this.retryOpenId = null;
    if (id === this.sessionId() && !this.error()) {
      this.sidebarOpen.set(false);
      return;
    }
    this.sessionId.set(id);
    this.messages.set([]);
    this.error.set(null);
    this.editingId.set(null);
    this.sidebarOpen.set(false);
    this.loadingHistory.set(true);
    try {
      const history = await firstValueFrom(this.api.history(id));
      if (this.sessionId() === id) this.messages.set(history.map(toMessage));
    } catch {
      if (this.sessionId() === id) {
        this.error.set('Could not load this chat.');
        this.retryOpenId = id;
      }
    } finally {
      if (this.sessionId() === id) {
        this.loadingHistory.set(false);
        this.scrollDown();
      }
    }
  }

  newChat(): void {
    this.sessionId.set(newSessionId());
    this.messages.set([]);
    this.error.set(null);
    this.editingId.set(null);
    this.sidebarOpen.set(false);
  }

  async send(text: string, context?: unknown): Promise<void> {
    text = text.trim();
    const sid = this.sessionId();
    if (!text || this.sending() || this.loadingHistory()) return;
    this.error.set(null);
    this.messages.update((m) => [...m, { role: 'user', text, time: new Date() }]);
    this.input.set('');
    this.sending.set(true);
    this.scrollDown();
    let started = false;
    const onText = (partial: string) => {
      if (this.sessionId() !== sid) return;
      if (!started) {
        started = true;
        this.messages.update((m) => [...m, { role: 'assistant', text: partial, time: new Date() }]);
      } else {
        this.messages.update((m) => [...m.slice(0, -1), { ...m[m.length - 1], text: partial }]);
      }
      this.scrollDown();
    };
    try {
      const { reply, ui } = await this.api.stream(text, sid, onText);
      if (this.sessionId() === sid) {
        onText(reply);
        if (ui) this.messages.update((m) => [...m.slice(0, -1), { ...m[m.length - 1], ui }]);
      }
      await this.refreshSessions();
      void this.loadHome(true);
    } catch (err) {
      if (this.sessionId() === sid) {
        if (started) this.messages.update((m) => m.slice(0, -1));
        if (!(err instanceof HttpErrorResponse && err.status === 401)) {
          this.error.set('Message failed to send.');
          this.lastFailed = text;
        }
      }
    } finally {
      this.sending.set(false);
      this.scrollDown();
    }
  }

  private patch(target: Message, changes: Partial<Message>): void {
    this.messages.update((list) => list.map((m) => (m === target ? { ...m, ...changes } : m)));
  }

  private addAssistant(partial: Partial<Message>): void {
    this.messages.update((m) => [...m, { role: 'assistant', text: '', time: new Date(), ...partial }]);
    this.scrollDown();
  }

  async answerPay(m: Message, yes: boolean): Promise<void> {
    this.patch(m, { answered: true });
    if (!yes) {
      this.addAssistant({ text: "No problem, I won't make a payment. Let me know if you need anything else." });
      return;
    }
    try {
      const items = await firstValueFrom(this.api.due());
      if (!items.length) {
        this.addAssistant({ text: 'You have no premium due right now.' });
        return;
      }
      this.addAssistant({
        text: '',
        card: { items, selected: items.length === 1 ? [items[0].payment_id] : [], authorized: false, paying: false, error: null },
      });
    } catch {
      this.addAssistant({ text: 'Sorry, I could not load your premiums right now. Please try again.' });
    }
  }

  cardTotal(card: PayCard): number {
    return card.items.filter((i) => card.selected.includes(i.payment_id)).reduce((sum, i) => sum + i.amount, 0);
  }

  toggleItem(m: Message, id: string): void {
    const card = m.card;
    if (!card || card.paying) return;
    const selected = card.selected.includes(id) ? card.selected.filter((s) => s !== id) : [...card.selected, id];
    this.patch(m, { card: { ...card, selected } });
  }

  toggleAuth(m: Message): void {
    if (m.card && !m.card.paying) this.patch(m, { card: { ...m.card, authorized: !m.card.authorized } });
  }

  async payNow(m: Message): Promise<void> {
    const card = m.card;
    if (!card || card.paying || !card.authorized || !card.selected.length) return;
    this.patch(m, { card: { ...card, paying: true, error: null } });
    try {
      const res = await firstValueFrom(this.api.pay(card.selected));
      const currency = res.paid[0]?.currency ?? 'USD';
      const refs = res.paid.map((p) => p.reference).join(', ');
      this.messages.update((list) => list.filter((x) => x !== m));
      this.addAssistant({
        text: `Payment of **${new Intl.NumberFormat('en-US', { style: 'currency', currency }).format(res.total)}** completed (demo payment). Reference: ${refs}.`,
      });
      void this.loadHome(true);
    } catch {
      this.patch(m, { card: { ...card, paying: false, error: 'Payment could not be completed. Please try again.' } });
      void this.loadHome(true);
    }
  }

  retry(): void {
    if (this.retryOpenId) {
      const id = this.retryOpenId;
      this.retryOpenId = null;
      this.sessionId.set('');
      void this.open(id);
      return;
    }
    const failed = this.lastFailed;
    if (!failed) return;
    this.messages.update((m) => m.slice(0, -1));
    void this.send(failed);
  }

  startRename(s: ChatSession, event: Event): void {
    event.stopPropagation();
    this.editingId.set(s.session_id);
    this.editTitle = s.title;
  }

  cancelRename(): void {
    this.editingId.set(null);
  }

  async saveRename(s: ChatSession): Promise<void> {
    if (this.editingId() !== s.session_id) return;
    const title = this.editTitle.trim();
    this.editingId.set(null);
    if (!title || title === s.title) return;
    try {
      const res = await firstValueFrom(this.api.rename(s.session_id, title));
      this.sessions.update((list) => list.map((x) => (x.session_id === s.session_id ? { ...x, title: res.title } : x)));
    } catch {
      this.sidebarError.set('Could not rename the chat.');
    }
  }

  async remove(s: ChatSession, event: Event): Promise<void> {
    event.stopPropagation();
    if (!window.confirm(`Delete "${s.title}"? This cannot be undone.`)) return;
    try {
      await firstValueFrom(this.api.remove(s.session_id));
      const wasActive = this.sessionId() === s.session_id;
      await this.refreshSessions();
      if (wasActive) {
        const next = this.sessions()[0];
        if (next) void this.open(next.session_id);
        else this.newChat();
      }
    } catch {
      this.sidebarError.set('Could not delete the chat.');
    }
  }

  onKey(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void this.send(this.input());
    }
  }

  private scrollDown(): AfterRenderRef {
    return afterNextRender(
      () => {
        const el = this.host.nativeElement.querySelector('#chat-scroll');
        if (el) el.scrollTop = el.scrollHeight;
      },
      { injector: this.injector },
    );
  }
}

function toMessage(m: HistoryMessage): Message {
  return { role: m.role, text: m.content, time: m.time ? new Date(m.time * 1000) : null };
}
