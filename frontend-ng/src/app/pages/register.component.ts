import { HttpErrorResponse } from '@angular/common/http';
import { Component, inject, signal } from '@angular/core';
import { AbstractControl, FormBuilder, ReactiveFormsModule, ValidationErrors, Validators } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { firstValueFrom } from 'rxjs';
import { AuthService } from '../core/auth.service';

function passwordsMatch(group: AbstractControl): ValidationErrors | null {
  return group.get('password')?.value === group.get('confirm')?.value ? null : { mismatch: true };
}

@Component({
  selector: 'app-register',
  imports: [ReactiveFormsModule, RouterLink],
  template: `
    <main class="auth-page">
      <form class="auth-card" [formGroup]="form" (ngSubmit)="submit()" novalidate>
        <div class="logo">PA</div>
        <h2>Create your account</h2>
        <p class="sub">Register to see your premiums and ask about your plan</p>

        <label>
          Display name
          <input type="text" formControlName="display_name" autocomplete="name" maxlength="50" autofocus />
        </label>
        @if (invalid('display_name')) {
          <div class="field-error">Enter your name.</div>
        }

        <label>
          Username
          <input type="text" formControlName="username" autocomplete="username" maxlength="30" />
        </label>
        @if (invalid('username')) {
          <div class="field-error">3-30 characters: letters, numbers and underscores.</div>
        }

        <label>Email <input type="email" formControlName="email" autocomplete="email" /></label>
        @if (invalid('email')) {
          <div class="field-error">Enter a valid email address.</div>
        }

        <label>
          Password
          <input type="password" formControlName="password" autocomplete="new-password" maxlength="72" />
        </label>
        @if (invalid('password')) {
          <div class="field-error">At least 8 characters.</div>
        }

        <label>
          Confirm password
          <input type="password" formControlName="confirm" autocomplete="new-password" maxlength="72" />
        </label>
        @if (form.hasError('mismatch') && form.controls.confirm.touched) {
          <div class="field-error">Passwords do not match.</div>
        }

        <button class="btn block" type="submit" [disabled]="loading()">
          {{ loading() ? 'Creating account...' : 'Create account' }}
        </button>
        @if (error()) {
          <div class="error" role="alert">{{ error() }}</div>
        }
        <p class="switch">Already registered? <a routerLink="/login">Sign in</a></p>
      </form>
    </main>
  `,
})
export class RegisterComponent {
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  readonly form = inject(FormBuilder).nonNullable.group(
    {
      display_name: ['', [Validators.required, Validators.maxLength(50), Validators.pattern(/\S/)]],
      username: ['', [Validators.required, Validators.pattern(/^[A-Za-z0-9_]{3,30}$/)]],
      email: ['', [Validators.required, Validators.pattern(/^[^@\s]+@[^@\s]+\.[^@\s]+$/)]],
      password: ['', [Validators.required, Validators.minLength(8), Validators.maxLength(72)]],
      confirm: ['', Validators.required],
    },
    { validators: passwordsMatch },
  );
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  invalid(name: 'display_name' | 'username' | 'email' | 'password'): boolean {
    const c = this.form.controls[name];
    return c.invalid && (c.touched || c.dirty);
  }

  async submit(): Promise<void> {
    this.form.markAllAsTouched();
    if (this.form.invalid || this.loading()) return;
    this.error.set(null);
    this.loading.set(true);
    const { display_name, username, email, password } = this.form.getRawValue();
    try {
      await firstValueFrom(
        this.auth.register({ display_name: display_name.trim(), username, email: email.trim(), password }),
      );
      await this.router.navigateByUrl('/chat');
    } catch (err) {
      const status = err instanceof HttpErrorResponse ? err.status : 0;
      this.error.set(
        status === 409
          ? 'That username or email is already registered.'
          : status === 422
            ? 'Please check your details and try again.'
            : 'Unable to reach the server. Try again.',
      );
    } finally {
      this.loading.set(false);
    }
  }
}
