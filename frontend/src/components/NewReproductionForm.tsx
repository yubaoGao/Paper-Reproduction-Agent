import { useState } from "react";
import { FilePdfOutlined, GithubOutlined, SendOutlined } from "@ant-design/icons";
import { Alert, Button, Form, Input, Upload } from "antd";
import type { UploadFile } from "antd";

interface Props {
  loading: boolean;
  error?: string;
  onSubmit: (input: { pdf: File; repositoryUrl: string; goal: string }) => void;
}

export function NewReproductionForm({ loading, error, onSubmit }: Props) {
  const [files, setFiles] = useState<UploadFile[]>([]);
  const [form] = Form.useForm<{ repositoryUrl: string; goal: string }>();

  const submit = (values: { repositoryUrl: string; goal: string }) => {
    const pdf = files[0]?.originFileObj;
    if (pdf) onSubmit({ pdf, ...values });
  };

  return (
    <div className="new-reproduction">
      <div className="welcome-mark"><span>RP</span></div>
      <span className="eyebrow">新建科研会话</span>
      <h1>您想复现什么研究？</h1>
      <p className="welcome-copy">提交论文、实现仓库和科研目标。ReproPilot 会在开始执行前分析并确认实验范围。</p>
      {error && <Alert type="error" showIcon message="无法创建复现任务" description={error} />}
      <Form form={form} layout="vertical" onFinish={submit} requiredMark={false} className="intake-form">
        <Form.Item label="论文 PDF" required>
          <Upload.Dragger
            accept="application/pdf,.pdf"
            maxCount={1}
            fileList={files}
            beforeUpload={() => false}
            onChange={({ fileList }) => setFiles(fileList.slice(-1))}
            onRemove={() => { setFiles([]); return true; }}
          >
            <FilePdfOutlined className="upload-icon" />
            <div className="upload-title">将论文拖到此处，或点击选择文件</div>
            <div className="upload-hint">PDF · 最大 50 MB</div>
          </Upload.Dragger>
        </Form.Item>
        <Form.Item
          label="GitHub 仓库"
          name="repositoryUrl"
          rules={[
            { required: true, message: "请输入仓库地址" },
            { pattern: /^https:\/\/github\.com\/[^/]+\/[^/?#]+(?:\.git)?$/, message: "请使用不含凭据的 GitHub HTTPS 地址" },
          ]}
        >
          <Input size="large" prefix={<GithubOutlined />} placeholder="https://github.com/organization/repository" />
        </Form.Item>
        <Form.Item
          label="复现目标"
          name="goal"
          rules={[{ required: true, whitespace: true, message: "请描述您希望复现的内容" }]}
        >
          <Input.TextArea rows={4} maxLength={10_000} showCount placeholder="例如：复现主要实验及全部消融实验。" />
        </Form.Item>
        <Button
          type="primary"
          size="large"
          htmlType="submit"
          loading={loading}
          disabled={!files[0]}
          icon={<SendOutlined />}
          block
        >
          分析论文与仓库
        </Button>
      </Form>
    </div>
  );
}
