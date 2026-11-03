import { Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import { Router, RouterLink } from '@angular/router';
import { firstValueFrom } from 'rxjs';
import { AuthService } from '../core/auth.service';

@Component({
  selector: 'app-login',
  imports: [ReactiveFormsModule, RouterLink],
  template: `
    <main class="auth-page">
      <form class="auth-card" [formGroup]="form" (ngSubmit)="submit()">
        <div class="logo">PA</div>
        <h2>Plan Assistant</h2>
        <p class="sub">Sign in to manage your plan</p>
        <label>Username <input type="text" formControlName="username" autocomplete="username" autofocus /></label>
        <label>Password <input type="password" formControlName="password" autocomplete="current-password" /></label>
        <button class="btn block" type="submit" [disabled]="loading() || form.invalid">
          {{ loading() ? 'Signing in...' : 'Sign in' }}
        </button>
        @if (error()) {
          <div class="error" role="alert">{{ error() }}</div>
        }
        <p class="switch">New here? <a routerLink="/register">Create an account</a></p>
      </form>
    </main>
  `,
})
export class LoginComponent {
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  readonly form = inject(FormBuilder).nonNullable.group({
    username: ['', Validators.required],
    password: ['', Validators.required],
  });
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  async submit(): Promise<void> {
    if (this.form.invalid || this.loading()) return;
    this.error.set(null);
    this.loading.set(true);
    const { username, password } = this.form.getRawValue();
    try {
      await firstValueFrom(this.auth.login(username.trim().toLowerCase(), password));
      await this.router.navigateByUrl('/chat');
    } catch (err) {
      const unauthorized = err instanceof HttpErrorResponse && err.status === 401;
      this.error.set(unauthorized ? 'Invalid username or password.' : 'Unable to reach the server. Try again.');
    } finally {
      this.loading.set(false);
    }
  }
}
