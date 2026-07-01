import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { ModeDial } from './ModeDial';

describe('ModeDial', () => {
  afterEach(() => {
    cleanup();
  });

  it('renders correctly in Plain mode', () => {
    const onToggle = vi.fn();
    render(<ModeDial mode="Plain" onToggle={onToggle} />);

    const button = screen.getByRole('switch');
    expect(button).toHaveClass('dial--plain');
    expect(button).toHaveClass('dial--md'); // default size
    expect(button).not.toBeDisabled();

    expect(screen.getByText('◌ PLAIN')).toBeInTheDocument();
    expect(screen.getByText('🔒 CYPHER')).toBeInTheDocument();
  });

  it('renders correctly in Cypher mode', () => {
    const onToggle = vi.fn();
    render(<ModeDial mode="Cypher" onToggle={onToggle} />);

    const button = screen.getByRole('switch');
    expect(button).toHaveClass('dial--cypher');
    expect(button).toHaveClass('dial--md');
  });

  it('calls onToggle when clicked', () => {
    const onToggle = vi.fn();
    render(<ModeDial mode="Plain" onToggle={onToggle} />);

    const button = screen.getByRole('switch');
    fireEvent.click(button);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('is disabled when disabled prop is true', () => {
    const onToggle = vi.fn();
    render(<ModeDial mode="Plain" onToggle={onToggle} disabled={true} />);

    const button = screen.getByRole('switch');
    expect(button).toBeDisabled();

    fireEvent.click(button);
    expect(onToggle).not.toHaveBeenCalled();
  });

  it('applies the correct size class', () => {
    const onToggle = vi.fn();
    render(<ModeDial mode="Plain" onToggle={onToggle} size="sm" />);

    const button = screen.getByRole('switch');
    expect(button).toHaveClass('dial--sm');
    expect(button).not.toHaveClass('dial--md');
  });

  it('applies the title prop', () => {
    const onToggle = vi.fn();
    const testTitle = "Toggle Mode";
    render(<ModeDial mode="Plain" onToggle={onToggle} title={testTitle} />);

    const button = screen.getByRole('switch');
    expect(button).toHaveAttribute('title', testTitle);
  });
});
