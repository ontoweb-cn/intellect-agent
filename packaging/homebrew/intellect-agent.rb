class HermesAgent < Formula
  include Language::Python::Virtualenv

  desc "Self-improving AI agent that creates skills from experience"
  homepage "https://intellect.ontoweb.cn"
  url "https://gitee.com/ontoweb/intellect-agent/releases/download/v0.6.4/intellect_agent-0.6.4.tar.gz"
  sha256 "<replace-with-release-asset-sha256>"
  license "MIT"

  depends_on "certifi" => :no_linkage
  depends_on "cryptography" => :no_linkage
  depends_on "libyaml"
  depends_on "python@3.12"
  depends_on "rust" => :build  # intellect_community_core Rust extension

  pypi_packages ignore_packages: %w[certifi cryptography pydantic]

  # Refresh resource stanzas after bumping the source url/version:
  #   brew update-python-resources --print-only intellect-agent

  def install
    venv = virtualenv_create(libexec, "python3.12")
    venv.pip_install resources
    venv.pip_install buildpath

    pkgshare.install "skills", "optional-skills"

    %w[intellect intellect-agent intellect-acp].each do |exe|
      next unless (libexec/"bin"/exe).exist?

      (bin/exe).write_env_script(
        libexec/"bin"/exe,
        INTELLECT_BUNDLED_SKILLS: pkgshare/"skills",
        INTELLECT_OPTIONAL_SKILLS: pkgshare/"optional-skills",
        intellect_MANAGED: "homebrew"
      )
    end
  end

  test do
    assert_match "Intellect Agent v#{version}", shell_output("#{bin}/intellect version")

    managed = shell_output("#{bin}/intellect update 2>&1")
    assert_match "managed by Homebrew", managed
    assert_match "brew upgrade intellect-agent", managed
  end
end
